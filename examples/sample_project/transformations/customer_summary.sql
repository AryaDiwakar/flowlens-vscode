CREATE TABLE customer_summary AS
WITH normalized AS (
    SELECT customer_id, country, age
    FROM customers
), unused_example AS (
    SELECT customer_id FROM customers
)
SELECT country, COUNT(*) AS customer_count, AVG(age) AS average_age
FROM normalized
GROUP BY country;
