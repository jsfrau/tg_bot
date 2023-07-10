import os
import openai
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv('OPENAI_API_KEY')
messages = []

while True:
    prompt = input('Запрос: ')

    # Command to reset the conversation context
    if prompt.lower().strip() == "/reset":
        print('Сбрасываю контекст.')
        messages = []
        continue

    # Command to exit the program
    if prompt.lower().strip() == "/exit":
        print('Выход из программы.')
        break

    # Command to show conversation history
    if prompt.lower().strip() == "/history":
        print("История разговора:")
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            print(f"{role.capitalize()}: {content}")
        print()
        continue

    message = {"role": "user", "content": prompt}
    messages.append(message)
    completion = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=messages
    )
    assistant_response = completion.choices[0].message.content
    messages.append({"role": "assistant", "content": assistant_response})
    print('ChatGPT:\n' + assistant_response + '\n')
