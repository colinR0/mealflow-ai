export interface Household { id: string; name: string }
export interface IngredientDictItem {
  id: string
  key: string
  name: string
  category: string
  default_unit: string
  aliases: string[]
}

export interface InventoryLot {
  id: string
  ingredient_id?: string
  name: string
  ingredient_key?: string
  outcome?: 'created' | 'updated' | 'restored'
  quantity_status: 'known' | 'unknown'
  quantity: string | null
  unit: string | null
  confirmed_at: string
  status: 'active' | 'consumed' | 'removed'
  version: number
  notes: string
}
export interface InventoryPage { items: InventoryLot[]; total: number; offset: number; limit: number; has_more: boolean }
export interface ShoppingItem {
  id: string
  name: string
  quantity_status: 'known' | 'unknown'
  status: 'pending' | 'purchased' | 'archived'
}
