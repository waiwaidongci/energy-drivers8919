<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue';
import {useResearch} from '../stores/research';
import {useWeather} from '../stores/weather';
import {useAuth} from '../stores/auth';
import {createStation,importForecasts,withdrawIssue,createAssignment,updateAssignment,deleteAssignment,getDayTrace} from '../api/weather';
import type {Trace} from '../api/weather';
import {useTask} from '../hooks/useTask';
import {number,date} from '../utils/format';
import PageHeading from '../components/common/PageHeading.vue';
import ErrorNotice from '../components/common/ErrorNotice.vue';
import EmptyState from '../components/common/EmptyState.vue';
import StatusBadge from '../components/common/StatusBadge.vue';

const lab=useResearch(),weather=useWeather(),auth=useAuth(),{busy,error,perform}=useTask();
const tab=ref<'days'|'forecasts'|'assignments'|'import'>('days');
const selected=ref(0),building=ref(''),statusFilter=ref(''),offset=ref(0);
const issueStation=ref(''),issueDay=ref(''),issueStatus=ref(''),issueOffset=ref(0);
const trace=ref<Trace|null>(null),traceDay=ref(''),traceBuilding=ref('');
const stationForm=ref({code:'',name:'',note:''});
const importForm=ref({source_file:'ls-weather-forecast.csv',csv_text:'station_code,target_day,published_at,received_at,revision,temperature_c\n'});
const applyForm=ref({building_code:'',station_code:'',valid_from:'',valid_to:'',note:''});
const editing=ref<number|null>(null);
const withdrawForm=ref({issue_id:0,occurred_at:'',reason:''});
const message=ref('');

const dataset=computed(()=>lab.datasets.find(d=>d.id===selected.value));
const buildings=computed(()=>[...new Set([...(dataset.value?.train_buildings||[]),...(dataset.value?.test_buildings||[])])].sort());
const selectedStation=computed(()=>weather.stations.find(s=>s.code===applyForm.value.station_code));
const missingPreview=computed(()=>weather.overview?.missing_day_count||0);

async function loadDays(){await weather.loadDays({building:building.value,status:statusFilter.value,offset:offset.value});}
async function loadIssues(){await weather.loadIssues({station:issueStation.value,day:issueDay.value,status:issueStatus.value,offset:issueOffset.value});}
async function loadAll(){await perform(async()=>{await lab.load();if(!selected.value)selected.value=lab.datasets[0]?.id||0;await weather.load();await Promise.all([loadDays(),loadIssues(),weather.loadMissing()]);});}
onMounted(loadAll);
watch([selected,building,statusFilter],()=>{offset.value=0;perform(loadDays);});
watch([issueStation,issueDay,issueStatus],()=>{issueOffset.value=0;perform(loadIssues);});

function submitStation(){perform(async()=>{await createStation(stationForm.value);stationForm.value={code:'',name:'',note:''};await weather.load();message.value='气象站已登记。';});}
function submitImport(){perform(async()=>{const result=await importForecasts(importForm.value);message.value=`导入${result.inserted}期新预报，${result.unchanged}期未变，重算${result.refreshed_building_days}个楼宇日；预报版本 v${result.forecast_version}。`;await weather.load();await Promise.all([loadDays(),loadIssues(),weather.loadMissing()]);});}
function withdraw(id:number){perform(async()=>{const result=await withdrawIssue(id,{occurred_at:withdrawForm.value.occurred_at||null,reason:withdrawForm.value.reason});message.value=`已撤回期次#${id}，保留原发布时间，重算${result.refreshed_building_days}个楼宇日。`;withdrawForm.value={issue_id:0,occurred_at:'',reason:''};await weather.load();await Promise.all([loadDays(),loadIssues()]);});}
function startTrace(row:{building_code:string;day:string}){traceBuilding.value=row.building_code;traceDay.value=row.day;perform(async()=>{trace.value=await getDayTrace(row.building_code,row.day);});}
function submitAssignment(){perform(async()=>{const payload={...applyForm.value,valid_to:applyForm.value.valid_to||null};if(editing.value){await updateAssignment(editing.value,payload);message.value=`归属#${editing.value}已更新，只重算该楼该区间的既有选取。`;}else{const result=await createAssignment(payload);message.value=`归属已建立，重算${result.recomputed_building_days}个楼宇日。`;}editing.value=null;applyForm.value={building_code:'',station_code:'',valid_from:'',valid_to:'',note:''};await weather.load();await loadDays();});}
function editAssignment(a:{id:number;building_code:string;station_code:string;valid_from:string;valid_to:string|null;note:string}){editing.value=a.id;applyForm.value={building_code:a.building_code,station_code:a.station_code,valid_from:a.valid_from,valid_to:a.valid_to||'',note:a.note};}
function removeAssignment(id:number){perform(async()=>{const result=await deleteAssignment(id);message.value=`归属#${id}已删除，重算${result.recomputed_building_days}个楼宇日。`;await weather.load();await loadDays();});}
const reasonText:Record<string,string>={no_station_assignment:'当日无气象站归属',no_forecast_before_cutoff:'截止前无预报',withdrawn_only:'截止前预报均已撤回'};
</script>
<template>
<PageHeading index="06" title="先锁预报，再谈未见楼宇" text="事后实测会高估预测效果：这里按楼宇逐期归属气象站，只取前一日00:00前已发布的最近预报；缺报单列，不借用实测值。">
  <RouterLink class="outline" to="/models">发起独立研究 ↗</RouterLink>
  <button class="outline" @click="loadAll">刷新资料库 ↻</button>
</PageHeading>
<ErrorNotice :message="error" @retry="loadAll"/>
<p v-if="message" class="success" role="status">{{message}}</p>
<section class="weather-summary">
  <div><small>预报版本</small><b>v{{weather.overview?.forecast_version??0}}</b></div>
  <div><small>归属版本</small><b>v{{weather.overview?.assignment_version??0}}</b></div>
  <div><small>气象站</small><b>{{weather.overview?.station_count??0}}</b></div>
  <div><small>已发布期次</small><b>{{weather.overview?.issue_count??0}}</b></div>
  <div><small>撤回期次</small><b>{{weather.overview?.withdrawn_count??0}}</b></div>
  <div><small>逐日选取</small><b>{{weather.overview?.selected_day_count??0}}</b></div>
  <div><small>缺报楼宇日</small><b class="negative">{{missingPreview}}</b></div>
</section>
<nav class="weather-tabs">
  <button v-for="item in [['days','逐日选取与追溯'],['forecasts','预报期次与撤回'],['assignments','站归属维护'],['import','导入与站点']]" :key="item[0]" :class="{active:tab===item[0]}" @click="tab=item[0] as typeof tab">{{item[1]}}</button>
</nav>

<section v-if="tab==='days'" class="weather-panel">
  <div class="weather-toolbar">
    <label>楼宇数据集<select v-model="selected"><option v-for="d in lab.datasets" :key="d.id" :value="d.id">{{d.name}} / {{d.building_count}}栋</option></select></label>
    <label>楼宇<select v-model="building"><option value="">全部楼宇</option><option v-for="code in buildings" :key="code" :value="code">{{code}}</option></select></label>
    <label>预报状态<select v-model="statusFilter"><option value="">全部</option><option value="issued">已选取</option><option value="missing">缺报</option></select></label>
  </div>
  <p class="weather-note">规则：{{weather.overview?.rule}}。选取依据写入不可变快照，研究开始后补报、撤回或改归属都不会改变该研究。实测气温仅作对照显示，不进入研究特征。</p>
  <div class="table-wrap"><table><thead><tr><th>楼宇 / 日期</th><th>归属站</th><th>状态</th><th>预报温度 °C</th><th>原发布时间</th><th>修订</th><th>实测对照 °C</th><th>选取版本</th><th></th></tr></thead><tbody><tr v-for="d in weather.days" :key="d.building_code+d.day" :class="{sealed:d.status==='missing'}"><td><b>{{d.building_code}}</b><small>{{d.day}}</small></td><td>{{d.station_code||'—'}}</td><td><span class="status" :class="d.status==='issued'?'completed':'failed'">{{d.status==='issued'?'已选取':'缺报'}}</span><small v-if="d.status==='missing'">{{reasonText[d.missing_reason||'']||d.missing_reason}}</small></td><td>{{d.forecast_temperature_c===null?'—':number(d.forecast_temperature_c,2)}}</td><td>{{d.published_at?date(d.published_at):'—'}}</td><td>{{d.revision??'—'}}</td><td>{{d.observed_temperature_c===null?'—':number(d.observed_temperature_c,2)}}<small>仅对照</small></td><td>v{{d.forecast_version}} / a{{d.assignment_version}}</td><td><button class="text-button" @click="startTrace(d)">追溯依据 ↗</button></td></tr></tbody></table></div>
  <div class="pagination"><span>共{{weather.dayTotal}}行 / {{offset+1}}—{{Math.min(offset+30,weather.dayTotal)}}</span><div><button :disabled="offset===0" @click="offset-=30;perform(loadDays)">← 前30行</button><button :disabled="offset+30>=weather.dayTotal" @click="offset+=30;perform(loadDays)">后30行 →</button></div></div>
  <EmptyState v-if="!weather.days.length&&!busy" title="尚无逐日选取" text="导入楼宇日数据并维护楼宇站归属后，系统会为每个楼宇日解析预报；缺报会单独列出。"/>
  <section v-if="trace" class="trace-card">
    <div class="section-heading"><h2>{{traceBuilding}} / {{traceDay}} 的选取依据</h2><button class="text-button" @click="trace=null">收起</button></div>
    <dl class="trace-facts">
      <div><dt>结论</dt><dd>{{trace.selection.status==='issued'?'已选取预报':'缺报（不借用实测）'}}<small v-if="trace.selection.missing_reason">{{reasonText[trace.selection.missing_reason]}}</small></dd></div>
      <div><dt>截止时间</dt><dd>{{date(trace.selection.cutoff_at)}}</dd></div>
      <div><dt>归属站</dt><dd>{{trace.selection.station_code||'无'}}<small v-if="trace.assignment_covering_day">{{trace.assignment_covering_day.valid_from}} 至 {{trace.assignment_covering_day.valid_to||'未定'}}</small></dd></div>
      <div><dt>选中温度</dt><dd>{{trace.selection.forecast_temperature_c===null?'—':number(trace.selection.forecast_temperature_c,3)}}</dd></div>
      <div><dt>原发布时间 / 修订</dt><dd>{{trace.selection.published_at?date(trace.selection.published_at):'—'}}<small>r{{trace.selection.revision??'—'}} · 接收 {{trace.selection.received_at?date(trace.selection.received_at):'—'}}</small></dd></div>
      <div><dt>选取版本</dt><dd>预报 v{{trace.selection.forecast_version}} / 归属 v{{trace.selection.assignment_version}}</dd></div>
    </dl>
    <p class="weather-note">{{trace.observed_note}}</p>
    <div class="table-wrap"><table><thead><tr><th>期次</th><th>原发布时间</th><th>接收时间</th><th>修订</th><th>温度</th><th>截止前</th><th>状态</th></tr></thead><tbody><tr v-for="i in trace.issue_candidates" :key="i.id" :class="{chosen:i.id===trace.selection.issue_id}"><td>#{{i.id}}</td><td>{{date(i.published_at)}}</td><td>{{date(i.received_at)}}</td><td>r{{i.revision}}</td><td>{{number(i.temperature_c,3)}}</td><td>{{i.before_cutoff?'是':'否'}}</td><td>{{i.status==='withdrawn'?'已撤回':'已发布'}}</td></tr></tbody></table></div>
    <details><summary>查看完整证据 JSON</summary><pre>{{JSON.stringify(trace.evidence,null,2)}}</pre></details>
  </section>
  <section class="missing-block">
    <div class="section-heading"><h2>缺报楼宇日</h2><span>从不以实测值替代</span></div>
    <div class="table-wrap"><table v-if="weather.missing.length"><thead><tr><th>楼宇 / 日期</th><th>原因</th><th>归属站</th><th>截止时间</th></tr></thead><tbody><tr v-for="m in weather.missing" :key="m.building_code+m.day"><td><b>{{m.building_code}}</b><small>{{m.day}}</small></td><td>{{reasonText[m.missing_reason]||m.missing_reason}}</td><td>{{m.station_code||'—'}}</td><td>{{date(m.cutoff_at)}}</td></tr></tbody></table><p v-else class="muted">当前没有缺报楼宇日。研究只使用已选取的预报值；若出现缺报，请在研究提交前补齐。</p></div>
  </section>
</section>

<section v-else-if="tab==='forecasts'" class="weather-panel">
  <div class="weather-toolbar">
    <label>气象站<select v-model="issueStation"><option value="">全部</option><option v-for="s in weather.stations" :key="s.code" :value="s.code">{{s.code}} / {{s.name}}</option></select></label>
    <label>预报日<input v-model="issueDay" type="date"></label>
    <label>状态<select v-model="issueStatus"><option value="">全部</option><option value="issued">已发布</option><option value="withdrawn">已撤回</option></select></label>
  </div>
  <p class="weather-note">改版是新期次（revision+1）；撤回保留原发布时间并单独留痕；迟到资料按发布者原始发布时间入库，接收时间只作审计。</p>
  <div class="table-wrap"><table><thead><tr><th>期次</th><th>站 / 预报日</th><th>原发布时间</th><th>接收时间</th><th>修订</th><th>温度 °C</th><th>状态</th><th>撤回时间</th><th></th></tr></thead><tbody><tr v-for="i in weather.issues" :key="i.id"><td>#{{i.id}}</td><td>{{i.station_code}}<small>{{i.target_day}}</small></td><td>{{date(i.published_at)}}</td><td>{{date(i.received_at)}}</td><td>r{{i.revision}}</td><td>{{number(i.temperature_c,3)}}</td><td><StatusBadge :status="i.status==='withdrawn'?'failed':'completed'"/></td><td>{{i.withdrawn_at?date(i.withdrawn_at):'—'}}</td><td><button v-if="i.status==='issued'&&auth.canEdit" class="text-button" @click="withdrawForm.issue_id=i.id">撤回此期 ↗</button></td></tr></tbody></table></div>
  <div class="pagination"><span>共{{weather.issueTotal}}期 / {{issueOffset+1}}—{{Math.min(issueOffset+50,weather.issueTotal)}}</span><div><button :disabled="issueOffset===0" @click="issueOffset-=50;perform(loadIssues)">← 前50期</button><button :disabled="issueOffset+50>=weather.issueTotal" @click="issueOffset+=50;perform(loadIssues)">后50期 →</button></div></div>
  <form v-if="withdrawForm.issue_id&&auth.canEdit" class="entry-form" @submit.prevent="withdraw(withdrawForm.issue_id)">
    <h2>撤回期次 #{{withdrawForm.issue_id}}</h2>
    <p class="input-note">撤回只影响此后重新选取的楼宇日；已接受的研究沿用其冻结快照。</p>
    <div class="form-grid"><label>撤回发生时间（可选）<input v-model="withdrawForm.occurred_at" placeholder="2026-08-01T09:00:00"></label><label>原因<input v-model="withdrawForm.reason" maxlength="600"></label></div>
    <button class="primary" :disabled="busy">确认撤回（保留原发布时间）→</button>
    <button type="button" class="text-button" @click="withdrawForm={issue_id:0,occurred_at:'',reason:''}">取消</button>
  </form>
</section>

<section v-else-if="tab==='assignments'" class="weather-panel">
  <p class="weather-note">按楼宇逐期归属：同一楼宇只允许不重叠的连续区间。更换归属只重算该楼宇在受影响日期内的既有选取，不扫描整库。</p>
  <div class="assignment-layout">
    <form v-if="auth.canEdit" class="research-form" @submit.prevent="submitAssignment">
      <h2>{{editing?'调整归属 #'+editing:'新增楼宇站归属'}}</h2>
      <label>楼宇代码<input v-model="applyForm.building_code" required minlength="2" :disabled="!!editing"></label>
      <label>气象站<select v-model="applyForm.station_code" required><option value="" disabled>选择站点</option><option v-for="s in weather.stations" :key="s.code" :value="s.code">{{s.code}} / {{s.name}}</option></select></label>
      <div class="form-grid"><label>开始日<input v-model="applyForm.valid_from" type="date" required></label><label>结束日（含，留空为未定）<input v-model="applyForm.valid_to" type="date"></label></div>
      <label>备注<input v-model="applyForm.note" maxlength="600"></label>
      <p class="input-note" v-if="selectedStation">{{selectedStation.name}} · {{selectedStation.note}}</p>
      <button class="primary" :disabled="busy">{{editing?'保存并重算受影响区间 →':'建立归属 →'}}</button>
      <button v-if="editing" type="button" class="text-button" @click="editing=null;applyForm={building_code:'',station_code:'',valid_from:'',valid_to:'',note:''}">取消编辑</button>
    </form>
    <div class="table-wrap"><table><thead><tr><th>楼宇</th><th>气象站</th><th>开始</th><th>结束</th><th>备注</th><th></th></tr></thead><tbody><tr v-for="a in weather.assignments" :key="a.id"><td><b>{{a.building_code}}</b></td><td>{{a.station_code}}</td><td>{{a.valid_from}}</td><td>{{a.valid_to||'未定'}}</td><td>{{a.note}}</td><td v-if="auth.canEdit"><button class="text-button" @click="editAssignment(a)">编辑</button> <button class="text-button" @click="removeAssignment(a.id)">删除</button></td></tr></tbody></table>
      <EmptyState v-if="!weather.assignments.length" title="尚未维护楼宇归属" text="为每栋楼宇指定逐期气象站；没有归属的楼宇日会记为缺报，而不会借用实测值。"/>
    </div>
  </div>
</section>

<section v-else class="weather-panel">
  <div class="weather-import-layout">
    <form v-if="auth.canEdit" class="research-form" @submit.prevent="submitImport">
      <h2>导入预报资料</h2>
      <label>来源文件<input v-model="importForm.source_file" required></label>
      <label>CSV内容<textarea v-model="importForm.csv_text" rows="9" required></textarea></label>
      <p class="input-note">必需列 station_code、target_day、published_at、temperature_c；可选 received_at、revision、status。published_at 为发布者原始时间，改版请提高 revision；重复导入同内容不新增期次。</p>
      <button class="primary" :disabled="busy">校验并导入 →</button>
    </form>
    <div>
      <form v-if="auth.canEdit" class="research-form" @submit.prevent="submitStation">
        <h2>登记气象站</h2>
        <label>站点代码<input v-model="stationForm.code" required maxlength="60"></label>
        <label>站点名称<input v-model="stationForm.name" required maxlength="160"></label>
        <label>覆盖说明<input v-model="stationForm.note" maxlength="600"></label>
        <button class="primary" :disabled="busy">登记站点 →</button>
      </form>
      <div class="table-wrap station-list"><table><thead><tr><th>代码</th><th>名称</th><th>说明</th></tr></thead><tbody><tr v-for="s in weather.stations" :key="s.code"><td><b>{{s.code}}</b></td><td>{{s.name}}</td><td>{{s.note}}</td></tr></tbody></table></div>
    </div>
  </div>
</section>
</template>
