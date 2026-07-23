import re

import pandas as pd


def extract_think_answer(text):
    if pd.isna(text):
        return "", ""
    if not isinstance(text, str):
        text = str(text)

    end_think_index = text.rfind("</think>")

    if end_think_index != -1:
        think_process = text[:end_think_index].replace("<think>", "").strip()
        answer = text[end_think_index + len("</think>"):].strip()
    else:
        think_process = ""
        answer = text.strip()

    answer_matches = re.findall(r'<answer>(.*?)</answer>', answer, re.DOTALL)
    if answer_matches:
        answer = answer_matches[-1].strip()

    json_matches = re.findall(r'```json(.*?)```', answer, re.DOTALL)
    if json_matches:
        answer = json_matches[-1].strip()

    return think_process, answer
