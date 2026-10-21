import axios from 'axios'
import { ElMessage } from 'element-plus'

const service = axios.create({
  baseURL: '/api',
  timeout: 60000
})

function firstFieldMessage(body) {
  const errors = body?.data?.errors
  if (Array.isArray(errors) && errors.length) {
    return errors[0]?.message || ''
  }
  return ''
}

service.interceptors.response.use(
  (response) => {
    const body = response.data
    if (!body || typeof body.code === 'undefined') {
      return body
    }
    if (body.code !== 200) {
      const message = body.message || '请求失败'
      if (!response.config?.silent) {
        ElMessage.error(message)
      }
      const error = new Error(message)
      error.code = body.code
      error.data = body.data
      error.isBusiness = true
      return Promise.reject(error)
    }
    return body
  },
  (error) => {
    if (axios.isCancel(error)) {
      return Promise.reject(error)
    }
    const body = error.response?.data
    let message =
      body?.message ||
      (error.code === 'ECONNABORTED' ? '请求超时，请稍后重试' : '') ||
      (error.response ? '请求失败' : '无法连接本地服务，请确认后端已启动')
    const fieldMessage = firstFieldMessage(body)
    if (fieldMessage) {
      message = `${message}：${fieldMessage}`
    }
    if (!error.config?.silent) {
      ElMessage.error(message)
    }
    error.businessData = body?.data
    return Promise.reject(error)
  }
)

export default service
