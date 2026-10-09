-- Scenario: PH-BIZ-004 | Validate Overloaded Contracts Count
-- Page: Overloaded Contracts | KPI: # of Overloaded Contracts
-- Default state: Overloaded contracts count on report load
SELECT 
    COUNT(DISTINCT [Contract Number]) AS [# of Overloaded Contracts]
FROM FACTS
WHERE [Contract Number] IS NOT NULL
  AND [Disposition Reason] = 'Overloaded';
