export interface Household { id: string; name: string }
export interface InventoryLot {
  id: string
  name: string
  quantity_status: 'known' | 'unknown'
  quantity: string | null
  unit: string | null
  confirmed_at: string
}
export interface ShoppingItem {
  id: string
  name: string
  quantity_status: 'known' | 'unknown'
  status: 'pending' | 'purchased' | 'archived'
}
