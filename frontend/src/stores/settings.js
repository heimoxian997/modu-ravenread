import { reactive } from 'vue'
import { getSettingsStatusApi } from '@/api/llmSettings'

const state = reactive({
  status: null,
  loading: false,
  error: null
})

let inFlight = null

export function useSettingsStore() {
  return state
}

export function refreshSettingsStatus() {
  if (inFlight) return inFlight
  state.loading = true
  inFlight = getSettingsStatusApi(true)
    .then((response) => {
      state.status = response.data
      state.error = null
      return response.data
    })
    .catch((error) => {
      state.status = null
      state.error = error
      return null
    })
    .finally(() => {
      state.loading = false
      inFlight = null
    })
  return inFlight
}
