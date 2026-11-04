import { defineConfig } from 'vite'
import * as uniModule from '@dcloudio/vite-plugin-uni'

const uniPlugin = ((uniModule as unknown as { default: { default: () => import('vite').Plugin[] } }).default.default)

export default defineConfig({
  plugins: [
    ...uniPlugin(),
    {
      name: 'mealflow-uni-h5-slot-flag',
      enforce: 'pre',
      transform(code, id) {
        if (!id.replace(/\\/g, '/').endsWith('/@dcloudio/uni-h5-vue/dist/vue.runtime.esm.js')) return
        // The H5 runtime updates this internal flag when the route's slots change.
        return code.replace('def(children, "_", type);', 'def(children, "_", type, true);')
      },
    },
  ],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: { '/api': process.env.MEALFLOW_API_PROXY || 'http://127.0.0.1:8000' },
  },
})
