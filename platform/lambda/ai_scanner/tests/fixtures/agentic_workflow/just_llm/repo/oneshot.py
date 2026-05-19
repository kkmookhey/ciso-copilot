from openai import OpenAI

client = OpenAI()
resp = client.chat.completions.create(model="gpt-4o-mini", messages=[])
print(resp.choices[0].message.content)
