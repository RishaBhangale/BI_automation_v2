-- Scenario: PH-BIZ-009 | Validate Missing PN Opportunities Revenue
-- Page: Pipeline Hygiene ISG | KPI: Unweighted Revenue of Missing PN Opps
-- Default state: ISG opportunities without Part Number unweighted revenue on report load
SELECT 
    SUM(f.[# Value]) AS [Unweighted Revenue of Missing PN Opps]
FROM FACTS f
INNER JOIN PIPELINE_DIMENSION p 
    ON f.[%Opportunity ID] = p.[%Opportunity ID]
WHERE f.[Measure] = 'Revenue USD @ Actual Rate'
  AND f.[IDG/ISG] = 'ISG'
  AND (f.[%Opportunity Product ID] IS NULL OR f.[%Opportunity Product ID] = '');
