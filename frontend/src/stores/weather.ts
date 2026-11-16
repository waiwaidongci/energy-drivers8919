import {defineStore} from 'pinia';
import {ref} from 'vue';
import {getWeatherOverview,getStations,getAssignments,getIssues,getWeatherDays,getMissingDays} from '../api/weather';
import type {WeatherOverview,Station,Assignment,Issue,WeatherDay} from '../api/weather';

export const useWeather=defineStore('weather',()=>{
 const overview=ref<WeatherOverview|null>(null),stations=ref<Station[]>([]),assignments=ref<Assignment[]>([]),issues=ref<Issue[]>([]),issueTotal=ref(0),days=ref<WeatherDay[]>([]),dayTotal=ref(0),missing=ref<{building_code:string;day:string;missing_reason:string;station_code:string|null;cutoff_at:string}[]>([]),missingTotal=ref(0);
 async function load(){[overview.value,stations.value,assignments.value]=await Promise.all([getWeatherOverview(),getStations(),getAssignments()]);}
 async function loadIssues(filter:{station?:string;day?:string;status?:string;offset?:number}={}){const result=await getIssues({...filter,limit:50});issues.value=result.rows;issueTotal.value=result.total;}
 async function loadDays(filter:{building?:string;start?:string;end?:string;status?:string;offset?:number}={}){const result=await getWeatherDays({...filter,limit:30});days.value=result.rows;dayTotal.value=result.total;}
 async function loadMissing(building=''){const result=await getMissingDays({building,limit:50});missing.value=result.rows;missingTotal.value=result.total;}
 return {overview,stations,assignments,issues,issueTotal,days,dayTotal,missing,missingTotal,load,loadIssues,loadDays,loadMissing};
});
