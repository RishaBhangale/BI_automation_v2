-- Scenario: PH-BIZ-012 | Validate Overdue Opportunities Total Quantity
-- Page: Overdue Opportunities | KPI: Quantity of Overdue Opportunities
-- Default state: Overall overdue opportunities quantity on report load
SELECT 
    SUM(f.[# Value]) AS [Quantity of Overdue Opportunities]
FROM FACTS f
INNER JOIN PIPELINE_DIMENSION p 
    ON f.[%Opportunity ID] = p.[%Opportunity ID]
WHERE f.[Measure] = 'Quantity'
  AND p.[Past Due] = -1
  AND f.[Open Pipe Flag] = 'Past Open';
