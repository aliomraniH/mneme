-- library-demo schema + seed data
-- Apply once against DATABASE_URL_NEON_PURPLE_KITE.
-- Safe to re-run: all DDL uses IF NOT EXISTS / ON CONFLICT DO NOTHING.

-- ── Tables ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS author (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    birth_year  INT,
    country     TEXT
);

CREATE TABLE IF NOT EXISTS book (
    id             SERIAL PRIMARY KEY,
    title          TEXT NOT NULL,
    author_id      INT  REFERENCES author(id),
    published_year INT,
    genre          TEXT,
    isbn           TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS member (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    joined_date DATE DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS loan (
    id          SERIAL PRIMARY KEY,
    book_id     INT REFERENCES book(id),
    member_id   INT REFERENCES member(id),
    loaned_at   TIMESTAMPTZ DEFAULT now(),
    returned_at TIMESTAMPTZ
);

-- ── Seed: authors ────────────────────────────────────────────────────────────

INSERT INTO author (name, birth_year, country) VALUES
  ('Gabriel García Márquez', 1927, 'Colombia'),
  ('Toni Morrison',          1931, 'USA'),
  ('Haruki Murakami',        1949, 'Japan'),
  ('Chimamanda Ngozi Adichie',1977,'Nigeria'),
  ('Dostoevsky, Fyodor',     1821, 'Russia'),
  ('Virginia Woolf',         1882, 'UK'),
  ('Jorge Luis Borges',      1899, 'Argentina'),
  ('Ursula K. Le Guin',      1929, 'USA'),
  ('Kazuo Ishiguro',         1954, 'UK'),
  ('Elena Ferrante',         1943, 'Italy')
ON CONFLICT DO NOTHING;

-- ── Seed: books ──────────────────────────────────────────────────────────────

INSERT INTO book (title, author_id, published_year, genre, isbn) VALUES
  ('One Hundred Years of Solitude', 1, 1967, 'Magic Realism',  '978-0-06-088328-7'),
  ('Love in the Time of Cholera',   1, 1985, 'Romance',        '978-0-14-028562-8'),
  ('Beloved',                       2, 1987, 'Historical',     '978-1-4000-3341-6'),
  ('Song of Solomon',               2, 1977, 'Literary',       '978-0-14-018999-1'),
  ('Norwegian Wood',                3, 1987, 'Romance',        '978-0-37-571853-2'),
  ('Kafka on the Shore',            3, 2002, 'Magic Realism',  '978-1-40-003294-5'),
  ('1Q84',                          3, 2009, 'Science Fiction','978-0-30-759351-5'),
  ('Purple Hibiscus',               4, 2003, 'Literary',       '978-1-61-695096-3'),
  ('Half of a Yellow Sun',          4, 2006, 'Historical',     '978-0-00-720028-3'),
  ('The Idiot',                     5, 1869, 'Classic',        '978-0-14-044792-6'),
  ('Crime and Punishment',          5, 1866, 'Classic',        '978-0-14-058905-2'),
  ('Mrs Dalloway',                  6, 1925, 'Modernist',      '978-0-15-662870-9'),
  ('To the Lighthouse',             6, 1927, 'Modernist',      '978-0-15-690739-8'),
  ('Ficciones',                     7, 1944, 'Short Stories',  '978-0-80-213030-3'),
  ('The Left Hand of Darkness',     8, 1969, 'Science Fiction','978-0-44-100731-5'),
  ('The Dispossessed',              8, 1974, 'Science Fiction','978-0-06-051275-4'),
  ('Never Let Me Go',               9, 2005, 'Dystopian',      '978-1-40-003395-9'),
  ('The Remains of the Day',        9, 1989, 'Literary',       '978-0-57-116921-7'),
  ('My Brilliant Friend',          10, 2011, 'Literary',       '978-1-60-945833-1'),
  ('The Story of the Lost Child',  10, 2014, 'Literary',       '978-1-60-945897-3')
ON CONFLICT DO NOTHING;

-- ── Seed: members ────────────────────────────────────────────────────────────

INSERT INTO member (name, email, joined_date) VALUES
  ('Alice Okafor',     'alice@mneme.test',   '2024-01-15'),
  ('Ben Nakamura',     'ben@mneme.test',     '2024-02-03'),
  ('Chloe Dubois',     'chloe@mneme.test',   '2024-02-20'),
  ('Daniel Silva',     'daniel@mneme.test',  '2024-03-10'),
  ('Elena Petrov',     'elena@mneme.test',   '2024-03-25'),
  ('Fatima Hassan',    'fatima@mneme.test',  '2024-04-08'),
  ('George Osei',      'george@mneme.test',  '2024-04-22'),
  ('Hannah Lee',       'hannah@mneme.test',  '2024-05-05'),
  ('Ibrahim Al-Rashid','ibrahim@mneme.test', '2024-05-18'),
  ('Julia Ferreira',   'julia@mneme.test',   '2024-06-01')
ON CONFLICT DO NOTHING;

-- ── Seed: loans (mix of active and returned) ─────────────────────────────────

INSERT INTO loan (book_id, member_id, loaned_at, returned_at) VALUES
  (1,  1,  '2025-01-02', '2025-01-16'),
  (5,  1,  '2025-02-01', NULL),
  (3,  2,  '2025-01-10', '2025-01-24'),
  (11, 2,  '2025-02-15', NULL),
  (6,  3,  '2025-01-20', '2025-02-03'),
  (14, 3,  '2025-03-01', NULL),
  (9,  4,  '2025-01-05', '2025-01-19'),
  (17, 4,  '2025-03-10', NULL),
  (12, 5,  '2025-02-08', '2025-02-22'),
  (2,  5,  '2025-03-15', NULL),
  (7,  6,  '2025-01-15', '2025-01-29'),
  (19, 6,  '2025-04-01', NULL),
  (15, 7,  '2025-02-20', '2025-03-06'),
  (4,  7,  '2025-04-05', NULL),
  (10, 8,  '2025-01-28', '2025-02-11'),
  (18, 8,  '2025-03-20', NULL),
  (16, 9,  '2025-02-05', '2025-02-19'),
  (8,  9,  '2025-04-10', NULL),
  (13, 10, '2025-01-12', '2025-01-26'),
  (20, 10, '2025-03-25', NULL)
ON CONFLICT DO NOTHING;
