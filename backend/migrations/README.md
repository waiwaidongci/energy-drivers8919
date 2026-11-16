# Schema v1 + forecast library

本地启动使用SQLAlchemy metadata创建初始表。自然键：users.email、building_datasets.name、building_days(dataset_id,building_code,day)、model_runs.name、driver_findings(run_id,feature)、retrofit_hypotheses.title、weather_stations.code、weather_forecast_issues(station_code,target_day,published_at,revision)、building_day_forecasts(building_code,day)、forecast_run_snapshots.run_id、forecast_snapshot_days(snapshot_id,building_code,day)。数值测量与解释列使用DOUBLE；选取依据与快照明细使用JSON。

## 预报资料库（追加式表）

- `weather_forecast_issues`：一期预报一行，只追加不更新温度。改版=新revision；迟到资料保留发布者原始`published_at`，`received_at`仅审计。撤回只改`status/withdrawn_at`并在`weather_forecast_withdrawals`留痕。
- `building_station_assignments`：楼宇逐期气象站归属，`valid_from`/`valid_to`（含）为闭区间，NULL表示未定；同一楼宇区间不得重叠。
- `building_day_forecasts`：每个楼宇日的选取结果与证据（截止时间、候选期次、选中期次、缺报原因），并记录解析时的`forecast_version`/`assignment_version`。缺报行`temperature_c`为NULL，绝不回填实测值。
- `weather_library_state`：单行版本号。导入、撤回、归属变更与建研究都会锁定该行，串行化版本推进。
- `forecast_run_snapshots` / `forecast_snapshot_days`：研究接受合同时冻结的逐行拷贝，worker只读快照；中断恢复沿用同一快照。

索引：`weather_forecast_issues(station_code,target_day,published_at,revision)`、`building_day_forecasts(station_code,day)`与`(status,day)`、`building_station_assignments(building_code,valid_from,valid_to)`。导入与归属变更只按文件中出现的站点/日期区间与单栋楼宇区间查询，不整库扫描。

本目录用于后续版本迁移；当前无生产升级迁移工具。真实升级应编写版本化DDL与回滚步骤，不能仅依赖create_all修改现有列。既有部署升级时需创建上述新表；旧`building_days.temperature_c`保留为实测原始值，不再作为模型特征。
