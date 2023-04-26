import os
import openai
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv('OPENAI_API_KEY')
messages = []
while True:
    prompt = input('Запрос: ')
    if "/reset" == prompt.lower().strip():
        print('Сбрасываю контекст.')
        messages = []
        continue
    message = {"role": "user", "content": prompt}
    messages.append(message)
    completion = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=messages
    )
    messages.append(completion.choices[0].message)
    print('ChatGPT:\n' + completion.choices[0].message.content + '\n')