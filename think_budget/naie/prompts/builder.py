import logging

from naie.prompts.templates import toolselect_5to1_prompt, toolselect_6to1_prompt, paramselect_prompt

logger = logging.getLogger(__name__)


def generate_prompt_question(row):
    capability_level2 = row.get('二级能力')
    auto_concate_prompt = row.get('是否自动拼接Prompt')

    if (capability_level2 != '工具选择') & (capability_level2 != '参数提取'):
        logger.error('数据类型错误')

    if auto_concate_prompt == '否':
        question = row.get('问题')
    elif auto_concate_prompt == '是' and capability_level2 == '参数提取':
        variables_paramselect = {"parameter_list": row.get('参数列表'), "experience_knowledge": row.get('经验知识'),
                                 "user_question": row.get('问题'), "process_history": row.get('历史节点描述'),
                                 "action_info": row.get('当前节点描述')}
        question = paramselect_prompt.format(**variables_paramselect)
    elif auto_concate_prompt == '是' and capability_level2 == '工具选择':
        api_list = row.get('候选API列表')
        variables_toolselect = {"api_list": api_list, "user_question": row.get('问题')}
        if '以上都不对' in api_list:
            question = toolselect_6to1_prompt.format(**variables_toolselect)
        else:
            question = toolselect_5to1_prompt.format(**variables_toolselect)
    else:
        question = "问题拼接失败"

    return question
