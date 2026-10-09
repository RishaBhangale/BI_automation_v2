-- Scenario: PH-BIZ-013 | Validate 7 Days Opportunities Total Quantity
-- Page: 7 Days Opportunities | KPI: Quantity of 7 Day Opportunities
-- Default state: Overall 7-day opportunities quantity on report load
SELECT 
    SUM(f.[# Value]) AS [Quantity of 7 Day Opportunities]
FROM FACTS f
INNER JOIN PIPELINE_DIMENSION p 
    ON f.[%Opportunity ID] = p.[%Opportunity ID]
WHERE f.[Measure] = 'Quantity'
  AND p.[Days To Close] <= 7;
