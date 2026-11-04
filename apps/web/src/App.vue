<script lang="ts">
import { defineComponent } from 'vue'
import { restoreSession } from './shared/session'

export default defineComponent({
  onLaunch() { void restoreSession() },
})
</script>

<style lang="scss">
// uni-app prepends platform styles in H5; @use cannot follow those rules.
@import './shared/theme.scss';
</style>
