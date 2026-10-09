-- Scenario: PH-BIZ-003 | Validate Overdue Contracts Count
-- Page: Pipeline Hygiene GA | KPI: # of Overdue Contracts
-- Default state: General Availability overdue contracts count on report load
SELECT 
    COUNT(DISTINCT [Contract Number]) AS [# of Overdue Contracts]
FROM FACTS
WHERE [Contract Number] IS NOT NULL
  AND [Open Pipe Flag] = 'Past Open';
