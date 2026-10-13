<script setup lang="ts">
import type {CourseStatusItem} from '../api'
defineProps<{items:CourseStatusItem[];teaching?:boolean}>()
function color(status:string):'success'|'danger'|'warning'|'info'{
  if(status==='CONFIRMED')return 'success'
  if(status==='CANCELLED')return 'danger'
  if(['REGISTERED','ASSIGNED','ALTERNATE'].includes(status))return 'warning'
  return 'info'
}
</script>
<template>
  <el-table :data="items" :empty-text="teaching?'本学期暂无任课记录':'本学期暂无选课记录'" class="course-status-table">
    <el-table-column label="课程 / 教学班" min-width="180"><template #default="{row}"><b>{{row.course}}</b><div class="subtle">{{row.course_code}} · {{row.code}}</div></template></el-table-column>
    <el-table-column v-if="!teaching" label="选择类型" width="150"><template #default="{row}">{{row.selection}}<div v-if="row.draft_choice" class="subtle">{{row.draft_choice}}（未生效）</div></template></el-table-column>
    <el-table-column label="当前状态" min-width="210"><template #default="{row}"><el-tag :type="color(row.status)" effect="light">{{row.status_label}}</el-tag></template></el-table-column>
    <el-table-column label="原因与说明" min-width="300"><template #default="{row}">{{row.reason}}</template></el-table-column>
    <el-table-column v-if="teaching" label="在班人数" width="100"><template #default="{row}">{{row.enrolled_count}} / {{row.capacity}}</template></el-table-column>
    <el-table-column v-else label="计费状态" width="165"><template #default="{row}">{{row.billing_label}}</template></el-table-column>
  </el-table>
</template>
