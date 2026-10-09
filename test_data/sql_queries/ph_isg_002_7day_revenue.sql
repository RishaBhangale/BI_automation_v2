-- Scenario: PH-BIZ-002 | Validate 7 Days Opportunities Revenue
-- Page: Pipeline Hygiene ISG | KPI: Unweighted Revenue of 7 Days Opps
-- Default state: ISG opportunities closing within 7 days revenue on report load
SELECT 
    SUM(f.[# Value]) AS [Unweighted Revenue of 7 Days Opps]
FROM FACTS f
INNER JOIN PIPELINE_DIMENSION p 
    ON f.[%Opportunity ID] = p.[%Opportunity ID]
WHERE f.[Measure] = 'Revenue USD @ Actual Rate'
  AND f.[IDG/ISG] = 'ISG'
  AND p.[Days To Close] <= 7;
