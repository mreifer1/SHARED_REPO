from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch
from huggingface_hub import login

login()  # enter your HF token when prompted

tokenizer = AutoTokenizer.from_pretrained("simulati0n/codebert-flakeflagger")
model = AutoModelForSequenceClassification.from_pretrained("simulati0n/codebert-flakeflagger")
model.eval()

def predict_flakiness(code: str):
    tokens = tokenizer(
        code,
        return_tensors="pt",
        truncation=True,
        max_length=256,
        padding=True
    )
    with torch.no_grad():
        output = model(**tokens)
    probs = torch.softmax(output.logits, dim=-1)
    prediction = torch.argmax(probs, dim=-1).item()
    return {
        "prediction": "flaky" if prediction == 1 else "not flaky",
        "confidence": probs[0][prediction].item()
    }

# Test it
result = predict_flakiness("""
@Deployment(resources={"org/activiti/engine/test/api/runtime/oneTaskProcess.bpmn20.xml"})
public void testSuspendProcessInstancesDuringProcessDefinitionSuspend() {
  int nrOfProcessInstances = 9;
  ProcessDefinition processDefinition = repositoryService.createProcessDefinitionQuery().singleResult();
  for (int i = 0; i < nrOfProcessInstances; i++) {
    runtimeService.startProcessInstanceByKey(processDefinition.getKey());
  }
  assertEquals(nrOfProcessInstances, runtimeService.createProcessInstanceQuery().count());
  assertEquals(0, runtimeService.createProcessInstanceQuery().suspended().count());
  assertEquals(nrOfProcessInstances, runtimeService.createProcessInstanceQuery().active().count());
  repositoryService.suspendProcessDefinitionById(processDefinition.getId(), true, null);
  for (ProcessInstance processInstance : runtimeService.createProcessInstanceQuery().list()) {
    assertTrue(processInstance.isSuspended());
  }
  for (Task task : taskService.createTaskQuery().list()) {
    try {
      taskService.complete(task.getId());
      fail("A suspended task shouldn't be able to be continued");
    } catch (ActivitiException e) {}
  }
  assertEquals(nrOfProcessInstances, runtimeService.createProcessInstanceQuery().count());
  assertEquals(nrOfProcessInstances, runtimeService.createProcessInstanceQuery().suspended().count());
  assertEquals(0, runtimeService.createProcessInstanceQuery().active().count());
  repositoryService.activateProcessDefinitionById(processDefinition.getId(), true, null);
  for (Task task : taskService.createTaskQuery().list()) {
    taskService.complete(task.getId());
  }
  assertEquals(0, runtimeService.createProcessInstanceQuery().count());
}
""")
print(result)