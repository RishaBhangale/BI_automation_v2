-- Scenario: PH-BIZ-006 | Validate Idle Opportunities Count
-- Page: Idle Opportunities ISG | KPI: # of Idle Opportunities
-- Default state: ISG opportunities idle for more than 30 days count on report load
SELECT 
    COUNT(DISTINCT p.[%Opportunity ID]) AS [# of Idle Opportunities]
FROM PIPELINE_DIMENSION p
INNER JOIN FACTS f 
    ON p.[%Opportunity ID] = f.[%Opportunity ID]
WHERE f.[IDG/ISG] = 'ISG'
  AND p.[Idle Date] > 30
  AND p.[Flag Open] = -1;
