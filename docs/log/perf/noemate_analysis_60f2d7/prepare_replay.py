"""Prepare selected full-context replay inputs and checks; never contact a server."""
import hashlib
import json
from pathlib import Path
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[3]
sys.path.insert(0,str(ROOT/'tools/dtb'))
import replay_tool_calls as replay

CASES=[
 ('60ebf04a9a7d439aa81af369edb9b492','knowledge_alarm_listing',True,
  '告警实体、ID、名称必须有输入检索证据；不得凭空补全；核对答案完整性。'),
 ('e1990c9fd1fb464d90f7d8020449117c','alarm_classification',True,
  '核对历史中的12条告警逐项归类、字段保真、数量与严重程度顺序；不得新编告警。'),
 ('176691c06a114e89aa7aff4f97ceac11','numeric_aggregation',True,
  '92.641+88.315+71.253+85.792=338.001，均值84.50025；正确说明数据日期与今天的差异。'),
 ('cbdecca2437a4c9eba58ebe502447054','retrieval_after_no_index',True,
  '检查Rag工具与参数、检索无结果后的处理、原因码适用协议；不把格式正确当知识正确。'),
 ('fb471d4c86924743896c58b6567e9379','log_time_window',True,
  '21:58:32之前2小时为19:58:32；区分建议检查项和已经证实的事实，保留输入中的约束。'),
 ('33f43d90e65c46f5b2216a6d9fd555c2','root_cause_plan',True,
  '保留上下文约束和分层排查顺序；不能把CPU/重传相关性写成已确认根因。'),
 ('61f86b4d1a7c45d3a1ad70100162fc7f','wrong_metric_diagnostic',False,
  '工具返回486指标与时间不匹配SCTP/08:00诉求；应明确缺失，不能把现有回答当正确真值。'),
 ('2dba468968ed434586d9bbdfc8c0eec4','empty_tool_arguments_diagnostic',False,
  'Response空arguments为协议诊断；还须处理工具数据网元与请求网元不匹配。'),
]

def main():
    rows={r['request_id']:r for r in map(json.loads,(OUT/'request_features.jsonl').read_text(encoding='utf-8').splitlines())}
    source=ROOT/'proxy/results/noemate/agent_20260917T123724Z_7bac2ba7/derived/27c30b983f78c49b86f3'
    cases=[]; index=[]
    for rid,category,eligible,check in CASES:
        r=rows[rid]; path=source/r['detail_json']; detail=json.loads(path.read_text(encoding='utf-8'))
        body=detail['request']; errors=replay.validate_conversation(body['messages'])
        assert not errors,(rid,errors)
        msg=detail['response']['choices'][0]['message']
        cases.append({'case_id':rid,'request_body':body,'response_data':msg,
          'replay_expectation':{'category':category,'expected_tool_call':bool(msg.get('tool_calls')),
             'expected_tool_names':[t['function']['name'] for t in msg.get('tool_calls',[])],
             'latency_eligible':eligible}})
        index.append({'request_id':rid,'category':category,'latency_eligible_after_quality_gate':eligible,
          'quality_status':'unscored_manual_criteria_only','quality_check':check,
          'reasoning_tokens_local':r['reasoning_tokens_local'],'request_sha256':r['request_sha256'],
          'source_detail':path.relative_to(ROOT).as_posix(),
          'source_detail_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
          'replay_status':'input_validated_not_executed'})
    path=OUT/'replay_cases.jsonl'
    path.write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases),encoding='utf-8')
    assert len(replay.load_cases(path,1,0))==len(CASES)
    (OUT/'replay_index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'{len(CASES)} full-context inputs validated; no inference or tool calls executed')

if __name__=='__main__':main()
