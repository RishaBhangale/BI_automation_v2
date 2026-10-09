-- Scenario: PH-BIZ-001 | Validate Overdue Opportunities Revenue
-- Page: Pipeline Hygiene ISG | KPI: Unweighted Revenue of Overdue Opps
-- Default state: ISG business unit overdue opportunities revenue on report load
SELECT 
    SUM(f.[# Value]) AS [Unweighted Revenue of Overdue Opps]
FROM FACTS f
INNER JOIN PIPELINE_DIMENSION p 
    ON f.[%Opportunity ID] = p.[%Opportunity ID]
WHERE f.[Measure] = 'Revenue USD @ Actual Rate'
  AND f.[IDG/ISG] = 'ISG'
  AND p.[Past Due] = -1
  AND f.[Open Pipe Flag] = 'Past Open';
