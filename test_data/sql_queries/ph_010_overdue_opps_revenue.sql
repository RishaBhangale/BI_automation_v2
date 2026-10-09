-- Scenario: PH-BIZ-010 | Validate Overdue Opportunities Total Revenue
-- Page: Overdue Opportunities | KPI: Revenue of Overdue Opportunities
-- Default state: Overall overdue opportunities weighted revenue on report load
SELECT 
    SUM(f.[# Value]) AS [Revenue of Overdue Opportunities]
FROM FACTS f
INNER JOIN PIPELINE_DIMENSION p 
    ON f.[%Opportunity ID] = p.[%Opportunity ID]
WHERE f.[Measure] = 'Weighted Revenue USD @ Actual Rate'
  AND p.[Past Due] = -1
  AND f.[Open Pipe Flag] = 'Past Open';
