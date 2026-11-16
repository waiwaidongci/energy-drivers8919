import {request,post} from './client';

export interface WeatherOverview{forecast_version:number;assignment_version:number;station_count:number;issue_count:number;withdrawn_count:number;assignment_count:number;selected_day_count:number;issued_day_count:number;missing_day_count:number;rule:string}
export interface Station{id:number;code:string;name:string;note:string}
export interface Assignment{id:number;building_code:string;station_code:string;valid_from:string;valid_to:string|null;note:string}
export interface Issue{id:number;station_code:string;target_day:string;published_at:string;received_at:string;revision:number;temperature_c:number;status:string;withdrawn_at:string|null;source_file:string}
export interface WeatherDay{building_code:string;day:string;station_code:string|null;status:string;missing_reason:string|null;forecast_temperature_c:number|null;observed_temperature_c:number|null;published_at:string|null;received_at:string|null;revision:number|null;cutoff_at:string;forecast_version:number;assignment_version:number}
export interface TraceIssue{id:number;published_at:string;received_at:string;revision:number;temperature_c:number;status:string;before_cutoff:boolean;withdrawn_at:string|null;source_file:string}
export interface Trace{selection:{status:string;missing_reason:string|null;station_code:string|null;assignment_id:number|null;issue_id:number|null;forecast_temperature_c:number|null;published_at:string|null;received_at:string|null;revision:number|null;cutoff_at:string;forecast_version:number;assignment_version:number;resolved_at:string};observed_temperature_c:number|null;observed_note:string;evidence:Record<string,unknown>;issue_candidates:TraceIssue[];withdrawals:{issue_id:number;occurred_at:string;received_at:string;reason:string;actor:string}[];assignments:Assignment[];assignment_covering_day:{id:number;station_code:string;valid_from:string;valid_to:string|null}|null}
export interface RunForecast{forecast_version:number;assignment_version:number;snapshot_fingerprint:string;counts:{dataset_rows:number;rows:number;issued_rows:number;missing_rows:number;missing_building_days:{building_code:string;day:string;reason?:string}[];missing_truncated:boolean};run_id:number;total:number;rows:{building_code:string;day:string;status:string;missing_reason:string|null;station_code:string|null;issue_id:number|null;temperature_c:number|null;published_at:string|null;received_at:string|null;revision:number|null;cutoff_at:string;used_in_model:boolean;feature_temperature_used:boolean}[];uses_temperature:boolean}

const query=(params:Record<string,string|number|undefined>)=>Object.entries(params).filter(([,v])=>v!==undefined&&v!=='').map(([k,v])=>`${k}=${encodeURIComponent(String(v))}`).join('&');
export const getWeatherOverview=()=>request<WeatherOverview>('/weather/overview');
export const getStations=()=>request<Station[]>('/weather/stations');
export const createStation=(data:unknown)=>post<Station>('/weather/stations',data);
export const importForecasts=(data:unknown)=>post<{accepted_rows:number;inserted:number;unchanged:number;withdrawals:number;refreshed_building_days:number;forecast_version:number}>('/weather/imports',data);
export const getIssues=(params:{station?:string;day?:string;status?:string;offset?:number;limit?:number})=>request<{total:number;rows:Issue[]}>('/weather/issues?'+query(params as Record<string,string|number>));
export const withdrawIssue=(id:number,data:unknown)=>post<{issue_id:number;refreshed_building_days:number;forecast_version:number}>(`/weather/issues/${id}/withdraw`,data);
export const getAssignments=(building='')=>request<Assignment[]>('/weather/assignments?'+query({building}));
export const createAssignment=(data:unknown)=>post<{assignment:Assignment;recomputed_building_days:number;assignment_version:number}>('/weather/assignments',data);
export const updateAssignment=(id:number,data:unknown)=>request<{assignment:Assignment;recomputed_building_days:number;assignment_version:number}>(`/weather/assignments/${id}`,{method:'PUT',body:JSON.stringify(data)});
export const deleteAssignment=(id:number)=>request<{deleted:number;recomputed_building_days:number}>(`/weather/assignments/${id}`,{method:'DELETE'});
export const getWeatherDays=(params:{building?:string;start?:string;end?:string;status?:string;offset?:number;limit?:number})=>request<{total:number;rows:WeatherDay[]}>('/weather/days?'+query(params as Record<string,string|number>));
export const getMissingDays=(params:{building?:string;offset?:number;limit?:number})=>request<{total:number;rows:{building_code:string;day:string;missing_reason:string;station_code:string|null;cutoff_at:string}[]}>('/weather/missing?'+query(params as Record<string,string|number>));
export const getDayTrace=(building:string,day:string)=>request<Trace>(`/weather/days/${encodeURIComponent(building)}/${day}/trace`);
export const getRunForecast=(id:number,params:{offset?:number;limit?:number}={})=>request<RunForecast>(`/runs/${id}/forecast?`+query(params as Record<string,string|number>));
