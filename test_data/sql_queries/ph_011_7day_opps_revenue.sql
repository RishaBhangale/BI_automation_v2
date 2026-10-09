-- Scenario: PH-BIZ-011 | Validate 7 Days Opportunities Total Revenue
-- Page: 7 Days Opportunities | KPI: Revenue of 7 Day Opportunities
-- Default state: Overall 7-day opportunities weighted revenue on report load
SELECT 
    SUM(f.[# Value]) AS [Revenue of 7 Day Opportunities]
FROM FACTS f
INNER JOIN PIPELINE_DIMENSION p 
    ON f.[%Opportunity ID] = p.[%Opportunity ID]
WHERE f.[Measure] = 'Weighted Revenue USD @ Actual Rate'
  AND p.[Days To Close] <= 7;
