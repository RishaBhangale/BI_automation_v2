-- Scenario: PH-BIZ-005 | Validate Underloaded Services
-- Page: Underloaded Services | KPI: Underloaded Services Contract
-- Default state: Underloaded services contract count on report load
SELECT 
    COUNT(DISTINCT [Contract Number]) AS [Underloaded Services Contract]
FROM FACTS
WHERE [Contract Number] IS NOT NULL
  AND [ServicesCategory_Flag] = 1
  AND [Disposition Reason] = 'Underloaded';
