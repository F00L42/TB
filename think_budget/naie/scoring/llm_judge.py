import os
import json

from naie.models.model_client import model_client
from naie.scoring.answer_extractor import extract_think_answer
from naie.config.settings import NAIE_CONFIG


def load_prompt_template():
    template_path = os.path.join(NAIE_CONFIG['tool_prompt_dir'], 'Telecom_tool_parameter.txt')
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()


def evaluate_answer(question, model_answer, reference, eval_model_name='Qwen3-32B'):
    template = load_prompt_template()
    prompt = template.format(question=question, reference=reference, model_answer=model_answer)

    client = model_client(eval_model_name)
    response = client.model_call_chat(prompt, 6400, enable_thinking=True)
    content = response.get('content', '')
    thinking, answer = extract_think_answer(content)
    json_answer = json.loads(answer)
    return json_answer
