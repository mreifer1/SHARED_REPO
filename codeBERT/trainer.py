import os
import random
import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset, DataLoader
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    get_linear_schedule_with_warmup,
    DataCollatorWithPadding,
)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    classification_report,
)


# CONFIG
CSV_PATH        = "Flakify_FlakeFlagger_dataset.csv"
MODEL_NAME      = "microsoft/codebert-base"
OUTPUT_DIR      = "./codebert-flakeflagger"
MAX_LENGTH      = 256          # Optimized from 512: Faster training, covers most tests
BATCH_SIZE      = 16          
EPOCHS          = 5
LEARNING_RATE   = 2e-5
WARMUP_RATIO    = 0.1
WEIGHT_DECAY    = 0.01
RANDOM_SEED     = 42
SAVE_BEST       = True
NUM_WORKERS     = 2            # Parallel data loading (CPU)


#Seeds for reproducibility
def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    # Ensure deterministic behavior for some algorithms
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


class FlakeDataset(Dataset):
    def __init__(self, codes: list, labels: list, tokenizer, max_length: int):
        self.encodings = tokenizer(
            codes,
            truncation=True,
            max_length=max_length,
            padding=False, # Dynamic padding happens in DataCollator
        )
        self.labels = labels

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        item = {
            "input_ids": self.encodings["input_ids"][idx],
            "attention_mask": self.encodings["attention_mask"][idx],
            "labels": torch.tensor(self.labels[idx], dtype=torch.long)
        }
        return item

def load_data(csv_path: str):
    df = pd.read_csv(csv_path)
    df = df[df["flaky"].isin([0, 1])].copy()
    df["flaky"] = df["flaky"].astype(int)
    df["code"] = df["final_code"].fillna(df["full_code"]).fillna("").str.strip()
    df = df[df["code"] != ""]
    df["project"] = df["project"].fillna("unknown_project")

    print(f"Dataset loaded: {len(df):,} samples")
    print(f"  Flaky: {(df['flaky']==1).sum():,} | Not-Flaky: {(df['flaky']==0).sum():,}")
    print(f"  Unique Projects: {df['project'].nunique()}")
    
    return df["code"].values, df["flaky"].values, df["project"].values

def verify_split(labels, set_name):
    n_flaky = np.sum(labels == 1)
    if n_flaky == 0:
        raise ValueError(f"CRITICAL ERROR: {set_name} set has 0 flaky samples. Change RANDOM_SEED.")
    print(f"  [Verify] {set_name} contains {n_flaky} flaky samples.")


#Train & Evaluation Helpers
def train(model, loader, optimizer, scheduler, device, loss_fn):
    model.train()
    total_loss = 0
    for batch in loader:
        optimizer.zero_grad()
        input_ids      = batch["input_ids"].to(device)
        attention_mask = batch["attention_mask"].to(device)
        labels         = batch["labels"].to(device)

        outputs = model(input_ids=input_ids, attention_mask=attention_mask)
        loss    = loss_fn(outputs.logits, labels)
        loss.backward()

        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        scheduler.step()
        total_loss += loss.item()
    return total_loss / len(loader)

@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    all_preds, all_labels = [], []
    for batch in loader:
        input_ids      = batch["input_ids"].to(device)
        attention_mask = batch["attention_mask"].to(device)
        labels         = batch["labels"].to(device)

        outputs = model(input_ids=input_ids, attention_mask=attention_mask)
        preds   = torch.argmax(outputs.logits, dim=-1)

        all_preds.extend(preds.cpu().numpy())
        all_labels.extend(labels.cpu().numpy())

    metrics = {
        "accuracy":  accuracy_score(all_labels, all_preds),
        "f1":        f1_score(all_labels, all_preds, zero_division=0),
        "precision": precision_score(all_labels, all_preds, zero_division=0),
        "recall":    recall_score(all_labels, all_preds, zero_division=0),
    }
    return metrics, all_preds, all_labels


# MAIN
def main():
    set_seed(RANDOM_SEED)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # 1. Load Data
    codes, labels, projects = load_data(CSV_PATH)

    # 2. Sequential Stratified Group Splitting
    sgkf = StratifiedGroupKFold(n_splits=6, shuffle=True, random_state=RANDOM_SEED)
    train_val_idx, test_idx = next(sgkf.split(codes, labels, groups=projects))

    X_train_val, y_train_val = codes[train_val_idx], labels[train_val_idx]
    groups_train_val = projects[train_val_idx]
    X_test, y_test = codes[test_idx], labels[test_idx]

    sgkf_val = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=RANDOM_SEED)
    train_idx, val_idx = next(sgkf_val.split(X_train_val, y_train_val, groups=groups_train_val))

    X_train, y_train = X_train_val[train_idx], y_train_val[train_idx]
    X_val, y_val     = X_train_val[val_idx], y_train_val[val_idx]

    print(f"\n--- Split Verification ---")
    verify_split(y_train, "TRAIN")
    verify_split(y_val, "VALIDATION")
    verify_split(y_test, "TEST")

    # 3. Tokenizer (Left-Side Truncation)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    tokenizer.truncation_side = "left" # Preserves assertions/expectations at end of test
    
    data_collator = DataCollatorWithPadding(tokenizer=tokenizer)

    train_ds = FlakeDataset(X_train.tolist(), y_train.tolist(), tokenizer, MAX_LENGTH)
    val_ds   = FlakeDataset(X_val.tolist(),   y_val.tolist(),   tokenizer, MAX_LENGTH)
    test_ds  = FlakeDataset(X_test.tolist(),  y_test.tolist(),  tokenizer, MAX_LENGTH)

    # Added num_workers for speed
    pin_memory = device.type == "cuda"
    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, 
                              collate_fn=data_collator, pin_memory=pin_memory, num_workers=NUM_WORKERS)
    val_loader   = DataLoader(val_ds,   batch_size=BATCH_SIZE, shuffle=False, 
                              collate_fn=data_collator, pin_memory=pin_memory, num_workers=NUM_WORKERS)
    test_loader  = DataLoader(test_ds,  batch_size=BATCH_SIZE, shuffle=False, 
                              collate_fn=data_collator, pin_memory=pin_memory, num_workers=NUM_WORKERS)

    # 4. Class Weights
    n_neg = np.sum(y_train == 0)
    n_pos = np.sum(y_train == 1)
    w_pos = n_neg / max(n_pos, 1)
    class_weights = torch.tensor([1.0, w_pos], dtype=torch.float).to(device)
    print(f"\nLoss Class Weights -> [not-flaky: 1.0, flaky: {w_pos:.2f}]")

    # 5. Model Setup
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME, num_labels=2).to(device)
    
    no_decay = ["bias", "LayerNorm.weight"]
    optimizer_grouped_parameters = [
        {"params": [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)], "weight_decay": WEIGHT_DECAY},
        {"params": [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)], "weight_decay": 0.0},
    ]
    optimizer = torch.optim.AdamW(optimizer_grouped_parameters, lr=LEARNING_RATE)
    
    total_steps = len(train_loader) * EPOCHS
    scheduler = get_linear_schedule_with_warmup(optimizer, int(total_steps * WARMUP_RATIO), total_steps)
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weights)

    # 6. Training Loop
    best_val_f1 = 0.0
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    #May increase epoch later to experiment with improving model
    for epoch in range(1, EPOCHS + 1):
        train_loss = train(model, train_loader, optimizer, scheduler, device, loss_fn)
        val_metrics, _, _ = evaluate(model, val_loader, device)

        print(f"Epoch {epoch}/{EPOCHS} | loss={train_loss:.4f} | val_f1={val_metrics['f1']:.4f} | val_acc={val_metrics['accuracy']:.4f}")

        if SAVE_BEST and val_metrics["f1"] > best_val_f1:
            best_val_f1 = val_metrics["f1"]
            model.save_pretrained(OUTPUT_DIR)
            tokenizer.save_pretrained(OUTPUT_DIR)
            print(f"  ✓ Saved best model (Val F1: {best_val_f1:.4f})")

    # 7. Final Test Evaluation
    print("\n── Final Test Evaluation (On Unseen Projects) ──")
    if SAVE_BEST and os.path.exists(OUTPUT_DIR):
        model = AutoModelForSequenceClassification.from_pretrained(OUTPUT_DIR).to(device)

    test_metrics, test_preds, test_labels = evaluate(model, test_loader, device)
    print(classification_report(test_labels, test_preds, target_names=["not-flaky", "flaky"]))

if __name__ == "__main__":
    main()