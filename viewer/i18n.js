/* research-dump web page: KO / EN.
 *
 * The page is written in Korean. In EN, a translator rewrites what is on screen:
 *  - interface text: the phrase table below (longest phrase first), then number patterns
 *  - data text (elements with class "dt": summaries, relation reasons, research topics):
 *    stored translations (demo data carries them) or, for your own data, the summary model in this browser
 *  - memos (class "raw"), inputs and text areas are never translated
 */
(() => {
  'use strict';

  const PHRASES = {
    // tabs, top bar, data panel
    '홈': 'Home', '논문 목록': 'Papers', '연구 주제': 'Research topics', '데이터': 'Data',
    '데모 데이터': 'Demo data', '내 저장소 연결': 'Connect my repository', '로그아웃': 'Log out',
    '읽기 목록 가져오기': 'Import reading list', '연구 스콥 · ': 'Scope · ', '저장소: ': 'Repository: ',
    '데모 데이터 (공개 논문만, 저장 안 됨)': 'Demo data (public papers only, nothing is saved)',
    'Layer 1 · 연구 주제': 'Layer 1 · Research topics', 'Layer 0 · 논문': 'Layer 0 · Papers',
    '+ 새 연구 주제': '+ New research topic', '전체 논문': 'All papers',
    '불러오는 중...': 'Loading...', '바뀐 파일 불러오는 중... ': 'Loading changed files... ',
    '불러오지 못했어요: ': 'Could not load: ',
    // sidebar
    '보기': 'View', '관계': 'Relations', '태그': 'Tags', '전체 논문 보기': 'Show all papers',
    '모든 논문을 보여줘요.': 'Showing every paper.', '기본: 좋아요 + 최근 4주 + ★ 논문': 'Default: liked + last 4 weeks + ★ papers',
    '숨긴 논문도 보기': 'Show hidden papers', '기업 논문만': 'Industry papers only', '★ 이상만': '★ and above only',
    '요약 없음만': 'Without summary only', '편 중 초록 없음 ': ' of them without abstract: ',
    '편 (초록이 없으면 자동 요약이 안 돼요)': ' (no abstract, no automatic summary)', '요약 없음 ': 'Without summary: ',
    'same-author (저자 노드)': 'same-author (author nodes)', '승인 전 관계(new) 보기': 'Show relations not yet approved (new)',
    '방법': 'Method', '목표': 'Goal', '세부 주제': 'Topic', '전체': 'All', '해제': 'None',
    // legend
    '좋아요': 'Liked', '일 이내)': ' days)', 'new (': 'new (', '★ 인용 속도 상위 20%': '★ top 20% citation speed', '★★ 상위 5%': '★★ top 5%',
    // graph messages
    '조건에 맞는 논문이 없어요. 왼쪽 "보기"에서 "전체 논문 보기"를 켜보세요.': 'No paper matches. Turn on "Show all papers" under View on the left.',
    '조건에 맞는 논문이 없어요.': 'No paper matches.',
    '아직 논문이 없어요. 왼쪽에서 읽기 목록을 가져오거나, 수집이 돌기를 기다려주세요.': 'No papers yet. Import the reading list on the left, or wait for the weekly collection.',
    '논문 노드를 누르거나, 왼쪽에서 연구 주제를 고르면 여기에 내용이 보여요.': 'Click a paper node, or pick a research topic on the left, to see details here.',
    '연구 주제를 누르면 여기에 내용이 보여요.': 'Click a research topic to see details here.',
    // paper panel
    '♥ 좋아요': '♥ Liked', '♡ 좋아요': '♡ Like', '북마크': 'Bookmark', '숨김 해제': 'Unhide', '숨김': 'Hide',
    '재요약': 'Re-summarize', '요약 중...': 'Summarizing...', '관계 찾기': 'Find relations', '관계 찾는 중...': 'Finding relations...',
    '연구 주제 추천': 'Suggest research topics', '추천 받는 중...': 'Suggesting...',
    '스크리닝: ': 'Screening: ', '메모': 'Memo', '메모 저장': 'Save memo',
    '메모 (나만 보는 생각, 읽은 소감, 아이디어)': 'Memo (your own notes and ideas)',
    '요약': 'Summary', '주장': 'Claim', '조건': 'Condition', '빈 곳': 'Gap', '아직 없음': 'Not yet',
    '요약: ': 'Summary: ', '아직 없음 (다음 실행 때 자동 요약)': 'not yet (summarized in the next run)', ' · 초록 기준': ' · from the abstract',
    '직접 입력': 'Edit by hand', '요약 일부가 비어 있어요. 확인해주세요.': 'Part of the summary is empty. Please check.',
    '초록 보기': 'Show abstract', '아직 관계가 없어요.': 'No relations yet.', '관계 직접 추가': 'Add a relation by hand',
    '대상': 'Target', 'Layer 1 · 연구 주제 ': 'Layer 1 · Research topics', '이유': 'Reason', '추가': 'Add',
    '승인': 'Approve', '거절': 'Reject', '저장': 'Save', '취소': 'Cancel', '신규': 'New', '기업': 'Industry',
    '외 ': '+', '명': '', ' 외': ' et al.',
    // references
    '베이스라인 · 주요 참고 논문': 'Baselines · key references', '불러오기': 'Load', '다시 불러오기': 'Reload',
    '마지막 확인: ': 'Last checked: ', '전체 참고문헌 보기 (': 'All references (', '방법론': 'method', '중요': 'influential',
    '저장소에 있음 · 연결됨': 'In repository · linked', '저장소에 있음': 'In repository', 'extends로 연결': 'Link as extends',
    '추가하기': 'Add', '추가 중...': 'Adding...',
    'Semantic Scholar의 인용 정보에서 이 논문이 방법론으로 쓰거나 중요하게 인용한 논문을 보여줘요.': 'Papers this paper uses as method or cites as influential, from Semantic Scholar citation data.',
    '방법론·중요 인용으로 표시된 참고문헌이 없어요. 아래 전체 목록을 봐주세요.': 'No references are marked as method or influential. See the full list below.',
    // research topics
    '후보': 'Candidate', '진행 중': 'Active', '보류': 'On hold', '접음': 'Retired', '모두': 'All', '직접': 'Mine', 'AI 추천': 'AI suggested',
    '수정': 'Edit', '진행할래요': 'Work on it', '접기': 'Fold', '근거': 'Basis', '관련 논문': 'Related papers',
    '저장소에서 관련 논문 찾기': 'Find related papers in the repository', '관련 논문 웹 검색': 'Search the web for related papers',
    '찾는 중...': 'Searching...', '검색 중...': 'Searching...', '저장소에 없는 논문 추가': 'Add a paper not in the repository',
    '추가하면 저장 → 요약 → 이 연구 주제와 다른 논문들에 대한 관계 초안까지 만들어요.': 'Adding saves, summarizes and drafts relations to this topic and to other papers.',
    '추가하면 저장 → 요약 → 연구 주제들에 대한 관계 초안까지 만들어요.': 'Adding saves, summarizes and drafts relations to your research topics.',
    'arXiv 번호, arXiv 링크 또는 논문 제목': 'arXiv id, arXiv link or paper title',
    '실험': 'Experiments', '실험 추가': 'Add experiment', '기록된 실험 없음': 'No experiments yet', '제목': 'Title', '상태': 'Status',
    '날짜': 'Date', '설정': 'Setup', '결과': 'Result', '계획': 'Planned', '완료': 'Done', '첫 실험': 'First experiment',
    '이전 버전 없음': 'No earlier versions', '연구 주제 수정': 'Edit research topic', '새 연구 주제': 'New research topic',
    '연구 주제 (한 줄)': 'Research topic (one line)', '근거 (왜 될 것 같은지, 아직 아무도 안 한 것)': 'Basis (why it should work, what nobody has done yet)',
    '바꾼 이유 (문장을 바꿀 때만)': 'Why it changed (only when the sentence changes)', '만들기': 'Create',
    '이 되고, 지금 문장은 버전 기록에 남아요.': ', and the current sentence stays in the version history.',
    '문장을 바꾸면 v': 'Changing the sentence makes it v', '추천 모델: ': 'Suggested by: ',
    '아직 없음. "수정"에서 적을 수 있어요.': 'Not yet. Write it under "Edit".',
    '아직 없어요. "관련 논문 찾기"를 누르거나, 아래에서 논문을 추가해보세요.': 'None yet. Search for related papers or add one below.',
    ': 이미 이 주제를 다룬 논문이 있을 수 있어요.': ': a paper may already cover this topic.',
    '연결 논문 ': 'linked papers ', '실험 ': 'experiments ',
    '연구 주제가 없어요. "+ 새 연구 주제"로 만들거나, 논문에서 "연구 주제 추천"을 눌러보세요.': 'No research topics. Create one with "+ New research topic" or press "Suggest research topics" on a paper.',
    '웹 추천 논문': 'Suggested papers from the web', '검색어: ': 'Queries: ', '관련 있는 논문을 찾지 못했어요.': 'No relevant papers found.',
    '좋은 학회': 'Top venue', '인용수': 'Citations', '인용 속도': 'Citation speed', '탑 기업': 'Top company', ' 순': '', '대표 논문': 'Seminal',
    '데모로 내보내기': 'Export to demo',
    // paper list
    '정렬': 'Sort', '들어온 날짜': 'Date added', '인기 (인용 속도)': 'Popularity (citation speed)', '표시': 'Show',
    '♥ 좋아요만': '♥ Liked only', '북마크만': 'Bookmarked only', '학회': 'Venue', '소속': 'Affiliation',
    '(누른 태그를 모두 가진 논문)': '(papers with all selected tags)', '제외됨 ': 'Excluded ', '키워드 통계': 'Keyword statistics',
    '키워드': 'Keyword', '이번 실행': 'Last run', '누적': 'Total', '포함 비율': 'Included',
    '아직 통계가 없어요. 다음 주간 실행부터 쌓여요.': 'No statistics yet. They start with the next weekly run.',
    '포함/수집. 수집은 했지만 거의 포함되지 않는 키워드는 config.json에서 빼도 돼요.': 'included / collected. Keywords that collect a lot but rarely get included can be removed from config.json.',
    '제목, 저자, 주장, 메모 검색': 'Search title, authors, claim, memo', '제목 검색': 'Search title',
    '논문 추가': 'Add paper', '펼치기': 'Expand', '그래프에서 보기': 'Show in graph', '요약 없음': 'No summary',
    ' · 들어온 날 ': ' · added ', '더 보기 (': 'More (', '편 남음)': ' left)', '포함시키기': 'Include',
    '스크리닝에서 최근 4주 동안 제외된 후보예요. 잘못 빠진 논문은 "포함시키기"로 넣을 수 있어요.': 'Candidates the screening excluded in the last 4 weeks. Use "Include" for any that were dropped by mistake.',
    '최근 4주 동안 제외된 후보가 없어요. (주간 실행이 bundle.json을 만든 뒤부터 보여요.)': 'No excluded candidates in the last 4 weeks (shown once a weekly run has built bundle.json).',
    '제외 이유: ': 'Excluded because: ', '제외 ': 'excluded ', '조건에 맞는 논문이 없어요. ': 'No paper matches. ',
    // requests
    '재요청': 'Retry', '취소하기': 'Cancel', ' 진행 중 · ': ' running · ', ' 실패: ': ' failed: ', ' 진행 중 (': ' running (',
    '참고문헌 불러오기': 'Loading references',
    // toasts
    '저장했어요.': 'Saved.', '저장하지 못했어요: ': 'Could not save: ', '데모 모드라서 저장되지 않아요 (새로고침하면 사라져요).': 'Demo mode: nothing is saved (gone after reload).',
    '이 기능은 내 저장소를 연결해야 쓸 수 있어요.': 'Connect your repository to use this.',
    '다시 요청했어요.': 'Requested again.', '취소했어요. 이미 돌고 있는 처리는 끝까지 갈 수 있어요.': 'Cancelled. A run already in progress may still finish.',
    '숨겼어요. "숨긴 논문도 보기"를 켜면 다시 보여요.': 'Hidden. Turn on "Show hidden papers" to see it again.',
    '연구 주제 추천을 요청했어요. 1~3분 뒤 연구 주제 탭에 후보가 생겨요.': 'Requested. Candidates appear in the Research topics tab in 1-3 minutes.',
    '관계 찾기를 요청했어요. 1~3분 뒤 이 논문의 관계에 후보가 생겨요.': 'Requested. Relation drafts appear on this paper in 1-3 minutes.',
    '관련 논문 찾기를 요청했어요. 1~3분 뒤 관련 논문에 후보가 생겨요.': 'Requested. Related paper drafts appear in 1-3 minutes.',
    '웹 검색을 요청했어요. 2~4분 뒤 웹 추천 논문이 보여요.': 'Requested. Suggestions appear in 2-4 minutes.',
    '논문 추가를 요청했어요. 저장, 요약, 관계 초안까지 1~3분 걸려요.': 'Requested. Saving, summarizing and drafting relations take 1-3 minutes.',
    '브라우저에서 바로 불러오지 못해서 GitHub Actions로 요청했어요. 1분쯤 걸려요.': 'Could not load from the browser, so GitHub Actions will do it (about a minute).',
    '연구 주제 추천 결과가 들어왔어요.': 'Research topic suggestions are in.', '관계 찾기 결과가 들어왔어요.': 'Relation drafts are in.',
    '관련 논문 찾기 결과가 들어왔어요.': 'Related paper drafts are in.', '웹 추천 논문이 들어왔어요.': 'Web suggestions are in.',
    '논문을 추가하고 요약과 관계 초안까지 만들었어요.': 'Paper added, summarized and linked.', '참고문헌을 불러왔어요.': 'References loaded.',
    '결과가 들어왔어요.': 'Results are in.', '포함시켰어요. 태그와 요약은 다음 주간 실행 때 채워져요.': 'Included. Tags and summary come with the next weekly run.',
    '이미 다 들어 있어요.': 'Already all there.', '가져오지 못했어요: ': 'Could not import: ', '재요약하지 못했어요: ': 'Could not re-summarize: ',
    '초록이 없어서 요약할 수 없어요.': 'No abstract to summarize.', '초록이 없어서 요약할 수 없어요': 'No abstract to summarize',
    '토큰 권한을 확인해주세요 (Contents: Read and write).': 'Check the token permissions (Contents: Read and write).',
    '저장소나 branch를 찾을 수 없어요.': 'Repository or branch not found.',
    // connect dialog
    '내 데이터 저장소 연결': 'Connect my data repository', '저장소 (owner/name)': 'Repository (owner/name)', '연결': 'Connect',
    '토큰은 이 브라우저에만 저장되고 api.github.com 말고는 어디에도 보내지 않아요. 공용 컴퓨터에서는 다 쓰고 로그아웃하세요.': 'The token is stored only in this browser and sent only to api.github.com. Log out when you are done on a shared computer.',
    '끌어서 너비 조절': 'Drag to resize',
    // export dialog
    '데모 데이터로 내보내기': 'Export to demo data', '내보내기': 'Export', '실험 포함 (공개돼도 되는 경우만)': 'Include experiments (only if they may be public)',
    '고른 연구 주제와 연결된 논문, 관계가 공개 저장소(research-dump)의 데모 데이터가 돼요. 메모는 넣지 않아요.': 'The selected research topics with their linked papers and relations become the demo data in the public repository (research-dump). Memos are never included.',
    '영어 번역 중...': 'Translating to English...', '내보내는 중...': 'Exporting...',
    // audit
    '감사': 'Audit', '감사 실행': 'Run audit', '감사 중...': 'Auditing...', '감사: ': 'Audit: ',
    '승인된 관계로 연결된 논문의 원문을 다른 모델이 다시 읽고 요약 → 관계 → 연구 주제 순으로 검사해요. 아무것도 바꾸지 않고 플래그만 표시해요.': 'Another model re-reads the full text of the papers linked by approved relations and checks the summary, then the relations, then the research topic. Nothing is changed: flags only.',
    '원문이 뒷받침': 'supported by the text', '종류가 다를 수 있음': 'type may differ', '원문 근거 없음': 'not supported by the text',
    '원문 없음 · 초록만': 'no full text · abstract only', '원문': 'full text', '요약 문제': 'summary issues', '요약 일치': 'summary matches',
    '감사 모델이 쓴 요약': "The auditor's summary", '검사하지 못했어요: ': 'Could not check: ', '이 연구 주제': 'this research topic',
    '유지': 'Keep', '문장 수정': 'Revise', '조건 좁히기': 'Narrow', '중복 위험': 'Duplicate risk', '접기 권장': 'Retire', '실험 추가': 'Add experiment',
    '진행 중 (': 'In progress (', '제안 문장': 'Suggested sentence', '제안 실험': 'Suggested experiment', '편 완료)': ' papers done)',
    '감사 결과가 들어왔어요.': 'Audit results are in.',
    '요약과 같은 모델로 검사했어요. 원문을 읽는다는 점은 다르지만 독립적인 검사는 아니에요.': 'Checked with the same model as the summaries: it reads the full text, but it is not an independent check.',
    '감사를 요청했어요. 논문 한 편에 1분 안팎이에요. 끝나면 이 화면에 플래그가 보여요.': 'Audit requested. About a minute per paper; flags appear here when done.',
  };

  const PATTERNS = [
    [/논문 (\d+)편 · 연구 주제 (\d+)개 · 관계 (\d+)개/g, '$1 papers · $2 topics · $3 relations'],
    [/요약 없음 (\d+)편 중 초록 없음 (\d+)편 \(초록이 없으면 자동 요약이 안 돼요\)/g, '$1 without summary, $2 of them without abstract (no abstract, no automatic summary)'],
    [/\(숨김 (\d+)\)/g, '($1 hidden)'],
    [/(\d+)편 중 10편/g, '10 of $1'],
    [/^논문 (\d+)$/g, 'Papers $1'], [/^제외됨 (\d+)$/g, 'Excluded $1'],
    [/(\d+)편 · 실험 (\d+)개/g, '$1 papers · $2 experiments'],
    [/(\d+)시간 (\d+)분/g, '$1h $2m'], [/(\d+)분 (\d+)초/g, '$1m $2s'], [/(\d+)초/g, '$1s'],
    [/(\d+)편/g, '$1 papers'], [/(\d+)개/g, '$1'], [/(\d+)명/g, '$1'],
    [/인용 (\d+)/g, '$1 citations'], [/월 ([\d.]+)회/g, '$1/month'],
    [/버전 기록 \((\d+)\)/g, 'Version history ($1)'], [/버전 기록/g, 'Version history'],
    [/duplicate (\d+)건/g, 'duplicate: $1'],
  ];

  const HANGUL = /[가-힣]/;
  const sorted = Object.keys(PHRASES).sort((a, b) => b.length - a.length);

  function ui(text) {
    if (!HANGUL.test(text)) return text;
    let out = text;
    for (const [re, rep] of PATTERNS.slice(0, 7)) out = out.replace(re, rep);
    for (const k of sorted) if (out.includes(k)) out = out.split(k).join(PHRASES[k]);
    for (const [re, rep] of PATTERNS.slice(7)) out = out.replace(re, rep);
    return out;
  }

  window.RDI18N = { ui, HANGUL };
})();
