// Native emulator uses the host bridge; physical devices require a reachable LAN URL.
let defaultApiBase = 'http://10.0.2.2:8000/api/v2'
// #ifdef H5
defaultApiBase = '/api/v2'
// #endif
export const API_BASE = import.meta.env.VITE_API_BASE_URL || defaultApiBase
