const PENDING_PROMPT_KEY = 'mealflow.assistant.pendingPrompt'

export function setAssistantPrompt(text: string): void {
  uni.setStorageSync(PENDING_PROMPT_KEY, text)
}

export function takeAssistantPrompt(): string {
  const value = uni.getStorageSync(PENDING_PROMPT_KEY)
  uni.removeStorageSync(PENDING_PROMPT_KEY)
  return typeof value === 'string' ? value.trim() : ''
}