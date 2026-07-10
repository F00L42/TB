import json


def preprocess_json_string(json_str):
    if not isinstance(json_str, str):
        return json_str

    processed = json_str.replace("'", '"')
    processed = processed.replace('None', 'null')
    processed = processed.replace('null', '""')
    processed = processed.replace(', ', ',')
    return processed


def is_list_equal_unordered(list1, list2):
    if len(list1) != len(list2):
        return False

    def preprocess_element(e):
        if isinstance(e, str):
            e = e.replace('null', '').replace('None', '')
            return preprocess_json_string(e)
        if isinstance(e, list):
            return [preprocess_element(x) for x in e]
        if isinstance(e, dict):
            return {k: preprocess_element(v) for k, v in e.items()}
        if e is None:
            return ""
        return e

    def to_hashable(obj):
        obj = preprocess_element(obj)
        if isinstance(obj, list):
            return tuple(to_hashable(e) for e in obj)
        if isinstance(obj, dict):
            return tuple(sorted((k, to_hashable(v)) for k, v in obj.items()))
        return obj

    set1 = set(to_hashable(e) for e in list1)
    set2 = set(to_hashable(e) for e in list2)

    return set1 == set2


def compute_similarity(a, b):
    if a is None:
        a = ""
    if b is None:
        b = ""

    if isinstance(a, str):
        a = a.replace('null', '').replace('None', '').strip()
    if isinstance(b, str):
        b = b.replace('null', '').replace('None', '').strip()

    if isinstance(a, list) and isinstance(b, list):
        return 1.0 if is_list_equal_unordered(a, b) else 0.0

    if isinstance(a, dict) and isinstance(b, dict):
        return 1.0 if sorted(a.items()) == sorted(b.items()) else 0.0

    if isinstance(a, str):
        a = preprocess_json_string(a)
    if isinstance(b, str):
        b = preprocess_json_string(b)

    return 1.0 if a == b else 0.0


def flatten_json(y, prefix=''):
    items = {}

    if isinstance(y, dict):
        for k, v in y.items():
            new_key = f"{prefix}.{k}" if prefix else k
            items.update(flatten_json(v, new_key))

    elif isinstance(y, list):
        sorted_list = sorted(y, key=lambda x: str(x))
        for i, v in enumerate(sorted_list):
            new_key = f"{prefix}[{i}]"
            items.update(flatten_json(v, new_key))

    else:
        items[prefix] = "" if y is None else y

    return items


def full_compare_json_entities(json1_str, json2_str):
    similarities = {}
    try:
        processed_str1 = preprocess_json_string(json1_str)
        processed_str2 = preprocess_json_string(json2_str)

        sim = compute_similarity(processed_str1, processed_str2)
        similarities[0] = {'similarity': sim, 'value1': processed_str1, 'value2': processed_str2}
    except Exception as e:
        similarities = '异常错误'
    return similarities


def compare_json_entities(json1_str, json2_str):
    try:
        json1_str = preprocess_json_string(json1_str)
        json2_str = preprocess_json_string(json2_str)

        json1 = json.loads(json1_str)
        json2 = json.loads(json2_str)

        flat_json1 = flatten_json(json1)
        flat_json2 = flatten_json(json2)

        all_keys = set(flat_json1.keys()).union(set(flat_json2.keys()))

        similarities = {}

        for key in all_keys:
            val1 = flat_json1.get(key)
            val2 = flat_json2.get(key)

            if val1 is None:
                if val2 in ("", None):
                    similarities[key] = {'similarity': 1.0, 'message': '值为空字段'}
                else:
                    similarities[key] = {'similarity': 0.0, 'message': 'JSON1中缺少该键'}
            elif val2 is None:
                if val1 in ("", None):
                    similarities[key] = {'similarity': 1.0, 'message': '值为空字段'}
                else:
                    similarities[key] = {'similarity': 0.0, 'message': 'JSON2中缺少该键'}
            else:
                sim = compute_similarity(val1, val2)
                similarities[key] = {'similarity': sim, 'value1': val1, 'value2': val2}

    except Exception as e:
        similarities = '异常错误'

    return similarities
