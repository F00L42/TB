from naie.scoring.answer_extractor import extract_think_answer
from naie.scoring.json_utils import compare_json_entities, full_compare_json_entities


def calculate_score(reference_answer, model_answer):
    think1, reference_answer = extract_think_answer(reference_answer)
    think2, model_answer = extract_think_answer(model_answer)

    if isinstance(reference_answer, str):
        json1_str = (reference_answer
                     .strip('"')
                     .replace('\"', '"')
                     .replace('\\', '')
                     .replace('\xa0', ' ')
                     .strip())
    else:
        json1_str = reference_answer

    line_result = str(model_answer).strip()
    json2_str = (line_result
                 .replace('```', '')
                 .replace('\xa0', ' ')
                 .strip())

    results = compare_json_entities(json1_str, json2_str)

    if isinstance(json1_str, str) and not (json1_str.startswith("{") or json1_str.startswith("[")):
        if json2_str.startswith("【") and json2_str.endswith("】"):
            json2_str = json2_str[1:-1]
        elif json2_str.startswith("[") and json2_str.endswith("]"):
            json2_str = json2_str[1:-1]
        results = full_compare_json_entities(json1_str, json2_str)

    min_sim = 1.0
    is_correct = 1
    if results == '异常错误':
        is_correct = 1
    else:
        for key, info in results.items():
            cur_sim = info['similarity']
            min_sim = min(cur_sim, min_sim)

        is_correct = 10 if min_sim >= 1 else 1

    return is_correct
