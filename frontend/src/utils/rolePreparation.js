export const ACTIVE_ROLE_JOBS = ['queued', 'running', 'pausing']
export const RESUMABLE_ROLE_JOBS = ['paused', 'interrupted', 'failed']
export const RESTART_ROLE_ERRORS = ['source_changed', 'source_plan_changed', 'character_identity_changed', 'model_changed', 'preparation_version_changed']

export const roleStatusLabel = (status) => ({
  ready: '批次已就绪', queued: '等待开始', running: '正在抽取', pausing: '正在暂停', paused: '已暂停',
  interrupted: '上次准备已中断', failed: '准备未完成', done: '资料已生成'
})[status] || '等待准备'

export const roleStatusType = (status) => status === 'done' ? 'success' : status === 'failed' ? 'danger' : ACTIVE_ROLE_JOBS.includes(status) ? 'warning' : 'info'

export function roleErrorMessage(code) {
  if (RESTART_ROLE_ERRORS.includes(code)) return '正文、人物称呼或模型配置已有变化，请重新预览并准备资料。已保存的旧版本仍可查看。'
  if (code === 'model_request_failed') return '模型请求未成功，请检查模型服务后继续。已完成的结果已保留。'
  if (code === 'model_request_timeout') return '模型响应超时，已停止发送新请求。已完成的结果已保留，可以稍后继续。'
  if (code === 'quote_omitted') return '引文含有省略内容，请改用原文中的短连续片段，或拆成多处依据后保存。'
  if (code === 'unknown_paragraph_reference') return '模型引用了本批以外的段落，请打开结果重新选择原文段落，或单独重试。'
  if (code === 'invalid_model_schema') return '模型已返回内容，但字段类型、数量或长度不符合要求。请查看原始返回和校验明细，可修订后本地校验。'
  if (code === 'model_output_truncated') return '模型输出被截断，尚未生成完整资料。请先查看已保存的返回，再决定修订或重新请求这一组。'
  if (code === 'unknown_observation_reference') return '模型引用了未提供的观测编号，结果未采用。请查看合成返回，核对依据编号。'
  if (code === 'unsupported_character_belief') return '模型把其他性质的观测当成人物自己的理解，结果未采用。请核对相关依据。'
  if (code === 'model_rate_limited') return '模型服务返回 429，任务已暂停，已完成结果已保留。请稍后继续。'
  if (code === 'process_interrupted') return '上次准备随服务退出而中断，可以从已保存的位置继续。'
  if (['request_too_large', 'synthesis_cannot_reduce'].includes(code)) return '当前资料超出模型处理范围，请换用更适合长文本的模型后重新准备。'
  if (['invalid_model_json', 'invalid_model_schema', 'quote_not_found', 'quote_ambiguous', 'wrong_character', 'unsupported_character_knowledge', 'reader_only_profile_reference', 'invalid_voice_reference'].includes(code)) return '模型返回的资料未通过核验，可以继续重试。已有的有效结果不会丢失。'
  return code ? '准备未完成，可以检查模型服务后重试。已完成的结果已保留。' : ''
}

export function roleRequestError(error) {
  if (error?.response?.status === 404) return '角色准备接口尚未加载，请重启后端后刷新页面。'
  return error?.message || '请求未成功，请稍后重试。'
}

export const roleFormKey = (form) => JSON.stringify({
  name: String(form.name || '').trim(), mode: form.mode, batch_chars: 8000
})

export const observationKindLabel = (kind) => ({
  identity: '身份', state: '人物状态', speech: '本人表达', action: '行为选择', belief: '个人判断',
  relationship: '关系', value: '价值取舍', boundary: '边界', counterexample: '例外与反证'
})[kind] || '人物观测'

export const knowledgeLabel = (kind) => ({
  experienced: '亲历', learned: '明确获知', believed: '个人相信', reader_only: '读者独知',
  unknown: '认知待确认', not_applicable: '不涉及认知'
})[kind] || '待确认'
