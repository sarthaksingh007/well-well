DROP VIEW IF EXISTS inspection_facts;
CREATE VIEW inspection_facts AS
WITH corrected AS (
  SELECT i.*,
         (SELECT c.corrected_value FROM corrections c
           WHERE c.target_id = i.inspection_id AND c.field = 'wellhead_temp_c' AND c.status = 'active'
           ORDER BY c.created_at DESC LIMIT 1) AS c_wellhead,
         (SELECT c.corrected_value FROM corrections c
           WHERE c.target_id = i.inspection_id AND c.field = 'ambient_temp_c' AND c.status = 'active'
           ORDER BY c.created_at DESC LIMIT 1) AS c_ambient,
         (SELECT c.corrected_value FROM corrections c
           WHERE c.target_id = i.inspection_id AND c.field = 'spill_detected' AND c.status = 'active'
           ORDER BY c.created_at DESC LIMIT 1) AS c_spill,
         (SELECT c.corrected_value FROM corrections c
           WHERE c.target_id = i.inspection_id AND c.field = 'spill_est_volume_bbl' AND c.status = 'active'
           ORDER BY c.created_at DESC LIMIT 1) AS c_volume
    FROM inspections i
),
effective AS (
  SELECT x.*, w.well_type, w.area_id, a.name AS area_name, w.lat, w.lon,
         COALESCE(CAST(x.c_wellhead AS REAL), x.wellhead_temp_c) AS eff_wellhead_temp_c,
         COALESCE(CAST(x.c_ambient AS REAL), x.ambient_temp_c) AS eff_ambient_temp_c,
         CASE WHEN x.c_spill IS NOT NULL THEN (x.c_spill = 'true') ELSE x.spill_detected END AS eff_spill,
         COALESCE(CAST(x.c_volume AS REAL), x.spill_est_volume_bbl) AS eff_spill_volume_bbl,
         COALESCE(
           (SELECT o.value FROM rule_overrides o
             WHERE o.rule = 'producing_min_delta_c' AND o.status = 'active'
               AND (o.well_type = w.well_type OR o.scope = 'fleet')
             ORDER BY o.created_at DESC LIMIT 1),
           r.producing_min_delta_c) AS producing_min_delta_c,
         (x.c_wellhead IS NOT NULL OR x.c_ambient IS NOT NULL OR x.c_spill IS NOT NULL OR x.c_volume IS NOT NULL)
           AS has_correction
    FROM corrected x
    JOIN wells w ON w.asset_id = x.asset_id
    LEFT JOIN areas a ON a.area_id = w.area_id
    LEFT JOIN well_type_rules r ON r.well_type = w.well_type
)
SELECT inspection_id, asset_id, well_type, area_id, area_name, lat, lon, inspected_at, inspection_date,
       inspector_user_id, method, ambient_temp_c, wellhead_temp_c, spill_detected, spill_area_pct,
       spill_est_volume_bbl, notes,
       eff_wellhead_temp_c, eff_ambient_temp_c, ROUND(eff_wellhead_temp_c - eff_ambient_temp_c, 1) AS temp_delta_c,
       producing_min_delta_c,
       (eff_wellhead_temp_c - eff_ambient_temp_c) >= producing_min_delta_c - 1e-9 AS producing,
       eff_spill, CASE WHEN eff_spill THEN eff_spill_volume_bbl END AS eff_spill_volume_bbl,
       has_correction
  FROM effective;

DROP VIEW IF EXISTS latest_inspection;
CREATE VIEW latest_inspection AS
SELECT f.* FROM inspection_facts f
 WHERE f.inspected_at = (SELECT MAX(i.inspected_at) FROM inspections i WHERE i.asset_id = f.asset_id);
