# MVP 로드맵

## A단계: 초기화

```bat
python scripts\paperops.py init
python scripts\paperops.py status
```

## B단계: 논문 수집

```bat
python scripts\paperops.py collect --limit 30
```

수집원:
- arXiv
- OpenAlex
- Crossref
- Semantic Scholar

## C단계: 관련도 점수화

```bat
python scripts\paperops.py score
python scripts\paperops.py digest --top 30
```

## D단계: PDF 처리

```bat
python scripts\paperops.py download-pdfs --limit 20
python scripts\paperops.py parse
```

## E단계: Paper Card / Evidence Matrix

```bat
python scripts\paperops.py cards --limit 20
python scripts\paperops.py extract-evidence --limit 20
```

## F단계: 작성 지원

```bat
python scripts\paperops.py outline
python scripts\paperops.py audit
```
