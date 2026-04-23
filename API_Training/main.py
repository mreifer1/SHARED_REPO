import json
import os
from pathlib import Path
from groq import Groq
from dotenv import load_dotenv
from training_prompts import *

HISTORY_FILE = 'chat_history.json'


def load_history():
    history_path = Path(HISTORY_FILE)
    if history_path.exists():
        try:
            with open(history_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return [{"role": "system", "content": "You are a helpful assistant."}]
    else:
        return [{"role": "system", "content": "You are a helpful assistant."}]

def save_history(messages):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(messages, f, indent=2)

def main():
    messages = load_history()
    load_dotenv()
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    try:
        while True:
            user_input = input("Chat with history: ")
            
            if user_input.lower() in ['exit', 'quit', 'q']:
                break

            if user_input == "train_flaky":
                user_input = flaky_tests

            if user_input == "train_non_flaky":
                user_input = non_flaky_tests

            if user_input == "testing1":
                user_input = test_code

            if user_input == "testing2":
                user_input = test_code2

            if user_input == "fewshot_prompting":
                user_input = fewshot1

            if user_input == "fewshot_prompting2":
                user_input = fewshot2

            if user_input == "fewshot_prompting3":
                user_input = fewshot3

            if user_input == "test_code_post_fewshot":
                user_input = test_code_post_fewshot

            if user_input == "test_code_post_fewshot2":
                user_input = test_code_post_fewshot2
                

            messages.append({"role": "user", "content": user_input})

            chat_completion = client.chat.completions.create(
                messages=messages,
                model="llama-3.3-70b-versatile",
                temperature=0.5,
                max_tokens=1024,
                top_p=1,
                stop=None,
                stream=False,
            )

            assistant_response = chat_completion.choices[0].message.content
            messages.append({"role": "assistant", "content": assistant_response})
            
            save_history(messages)
            print(assistant_response + "\n")

    except KeyboardInterrupt:
        print("\nChat session interrupted.")
    finally:
        save_history(messages)

if __name__ == "__main__":
    main()