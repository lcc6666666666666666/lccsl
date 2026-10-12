<script setup lang="ts">
import type {Offering} from '../api'
import {time} from '../api'
defineProps<{offerings:Offering[]}>()
</script>
<template><div class="timetable"><div class="day" v-for="(day,i) in ['周一','周二','周三','周四','周五','周六','周日']" :key="day"><div class="day-label">{{day}}</div><template v-for="o in offerings" :key="o.id"><div v-for="(m,j) in o.meetings.filter(m=>m.weekday===i+1)" :key="j" class="class-block"><b>{{o.name}}</b><span>{{time(m.start_minute)}}–{{time(m.end_minute)}}</span><span>{{m.room}}</span><small>第{{m.week_start}}–{{m.week_end}}周</small></div></template></div></div></template>
