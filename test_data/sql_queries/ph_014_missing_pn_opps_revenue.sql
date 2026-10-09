-- Scenario: PH-BIZ-014 | Validate Missing PN Opportunities Total Revenue
-- Page: Missing PN Opportunities | KPI: Revenue of Missing PN Opportunities
-- Default state: Overall missing Part Number opportunities weighted revenue on report load
SELECT 
    SUM(f.[# Value]) AS [Revenue of Missing PN Opportunities]
FROM FACTS f
INNER JOIN PIPELINE_DIMENSION p 
    ON f.[%Opportunity ID] = p.[%Opportunity ID]
WHERE f.[Measure] = 'Weighted Revenue USD @ Actual Rate'
  AND (f.[%Opportunity Product ID] IS NULL OR f.[%Opportunity Product ID] = '');
