export interface OfflineIngredient {
  key?: string
  name: string
  quantity?: number | null
  unit?: string | null
  optional?: boolean
}

export interface OfflineRecipe {
  id: string
  name: string
  servings: number
  duration_minutes: number
  category?: string
  tags: string[]
  taste_tags: string[]
  allergens: string[]
  steps: string[]
  source_name: string
  source_url?: string | null
  license_note?: string
  ingredients: OfflineIngredient[]
  search_tokens?: string[]
}

export interface OfflineSkill {
  id: string
  title: string
  document_type: string
  content_version: number
  source_name: string
  source_url?: string
  text: string
  search_tokens?: string[]
}

export interface OfflineBundle {
  schema_version?: number
  data_version?: string
  generated_at?: string
  recipes: OfflineRecipe[]
  skills: OfflineSkill[]
  constraints?: unknown[]
  disclaimer?: string
  offline_rules?: Record<string, unknown>
}

export interface OfflineHit<T> {
  item: T
  score: number
}

const SEPARATORS = /[\s,，、;；/]+/

function text(value: unknown): string {
  return typeof value === 'string' ? value.toLowerCase().trim() : ''
}

function queryTokens(query: string): string[] {
  const tokens = new Set<string>()
  for (const chunk of text(query).split(SEPARATORS).filter(Boolean)) {
    tokens.add(chunk)
    const chars = Array.from(chunk)
    // CJK queries often arrive without spaces; bigrams give a small, deterministic fallback.
    if (chars.length > 2) {
      for (let index = 0; index < chars.length - 1; index++) {
        tokens.add(chars[index] + chars[index + 1])
      }
    }
  }
  return [...tokens]
}

function recipeTokens(recipe: OfflineRecipe): string[] {
  if (Array.isArray(recipe.search_tokens) && recipe.search_tokens.length) {
    return recipe.search_tokens.map((token) => text(token)).filter(Boolean)
  }
  return [recipe.name, recipe.category || '', ...(recipe.tags || []), ...(recipe.taste_tags || []),
    ...(recipe.ingredients || []).map((item) => item.name)].map((item) => text(item)).filter(Boolean)
}

function skillTokens(skill: OfflineSkill): string[] {
  if (Array.isArray(skill.search_tokens) && skill.search_tokens.length) {
    return skill.search_tokens.map((token) => text(token)).filter(Boolean)
  }
  return [skill.title, skill.text].map((item) => text(item)).filter(Boolean)
}

function score(tokens: string[], query: string[]): number {
  if (!query.length) return 0
  const haystack = tokens.join(' ')
  let value = 0
  for (const token of query) {
    if (tokens.includes(token)) value += 3
    else if (haystack.includes(token)) value += 1
  }
  return value
}

export function parseOfflineBundle(raw: unknown): OfflineBundle | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Partial<OfflineBundle>
  if (!Array.isArray(value.recipes) || !Array.isArray(value.skills)) return null
  return {
    ...value,
    recipes: value.recipes,
    skills: value.skills,
  } as OfflineBundle
}

export function searchOffline(bundle: OfflineBundle, query: string, limit = 3): {
  recipes: OfflineHit<OfflineRecipe>[]
  skills: OfflineHit<OfflineSkill>[]
} {
  const tokens = queryTokens(query)
  if (!tokens.length) return { recipes: [], skills: [] }
  return {
    recipes: bundle.recipes
      .map((item) => ({ item, score: score(recipeTokens(item), tokens) }))
      .filter((hit) => hit.score > 0)
      .sort((left, right) => right.score - left.score || left.item.name.localeCompare(right.item.name))
      .slice(0, limit),
    skills: bundle.skills
      .map((item) => ({ item, score: score(skillTokens(item), tokens) }))
      .filter((hit) => hit.score > 0)
      .sort((left, right) => right.score - left.score || left.item.title.localeCompare(right.item.title))
      .slice(0, limit),
  }
}

export function ingredientLabel(ingredient: OfflineIngredient): string {
  if (ingredient.quantity !== null && ingredient.quantity !== undefined) {
    return `${ingredient.name} ${ingredient.quantity}${ingredient.unit || ''}`
  }
  // The imported corpus has no amounts. Do not manufacture a quantity.
  return ingredient.name
}

export function formatOfflineRecipe(
  recipe: OfflineRecipe,
  options: { dataVersion?: string; note?: string; disclaimer?: string },
): string {
  const lines = [`【离线·菜谱检索】${recipe.name}`]
  if (options.note) lines.push(options.note)
  if (recipe.ingredients.length) {
    lines.push(`食材：${recipe.ingredients.map(ingredientLabel).join('、')}`)
  }
  if (recipe.steps.length) {
    lines.push('步骤：')
    lines.push(...recipe.steps.map((step, index) => `${index + 1}. ${step}`))
  }
  if (recipe.source_name) lines.push(`来源：${recipe.source_name}`)
  lines.push(`离线数据版本：${options.dataVersion || '未知'}；离线模式不联网、不生成新内容。`)
  if (options.disclaimer) lines.push(options.disclaimer)
  return lines.join('\n')
}

export function formatOfflineSkill(
  skill: OfflineSkill,
  options: { dataVersion?: string; note?: string },
): string {
  const lines = [`【离线·烹饪技巧】${skill.title}`]
  if (options.note) lines.push(options.note)
  lines.push(skill.text)
  lines.push(`离线数据版本：${options.dataVersion || '未知'}；离线模式不联网、不生成新内容。`)
  return lines.join('\n')
}