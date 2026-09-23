"""Deliberate regressions for the probe checks.

Each tuple is (label, repository path, original, broken, expected failed assertion).
"""

INJECTIONS = [('I1 恢复 trust_env=True（让系统/环境代理接管模型服务流量）',
  'app/llm.py',
  'return httpx.Client(timeout=timeout, trust_env=settings.llm_trust_env_proxy)',
  'return httpx.Client(timeout=timeout, trust_env=True)',
  '未被送到代理'),
 ('I2 恢复恒定发送 Bearer 头（key 为空时生成带尾随空格的非法头）',
  'app/llm.py',
  '    if key:',
  '    if True:  # 注入缺陷',
  '空 key 时不构造 Authorization 头'),
 ('I3 恢复“任何 HTTP 响应都算可达”（把网关错误当作链路可达）',
  'app/llm_health.py',
  'if response.status_code in _GATEWAY_ERROR_CODES:',
  'if False:',
  ('代理返回 502 时判定为不可用', '信息里点明具体的网关状态码', '信息里指出代理这一常见原因')),
 ('I4 去掉探测里的 key 校验（/api/ready 会在提问必然失败时仍然变绿）',
  'app/llm_health.py',
  '        if not effective_api_key():',
  '        if False:',
  '空 key 时判定为未就绪'),
 ('I5 启动校验退回裸属性判断（只有空白的 key 又能通过启动校验）',
  'app/config.py',
  '        llm_key = effective_key(self.deepseek_api_key)',
  '        llm_key = self.deepseek_api_key',
  '只有空白的 DEEPSEEK_API_KEY 也在启动时被拒绝')]
