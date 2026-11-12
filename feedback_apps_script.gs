/*
  Q 시리즈 - 사용 의견 받는 구글 시트용 코드 (앱스 스크립트)
  Q 이미지 편집기와 Q 텍스트 추출이 같은 시트를 함께 쓸 수 있도록, 맨 끝에 '프로그램' 열을 추가한 버전이다.

  [이미 쓰고 있는 시트에 적용하는 법]
  1. 의견 시트의 [확장 프로그램] > [Apps Script] 를 열고, 이 파일 내용으로 통째로 바꾼 뒤 저장한다.
  2. [배포] > [배포 관리] > 연필 아이콘 > 버전을 '새 버전' 으로 > [배포]. (주소는 그대로)
  3. 시트 '의견' 탭의 F1 칸에 '프로그램' 이라고 적는다. (기존 열 순서는 그대로 두고 끝에만 열이 늘어난다)
     예전 줄(프로그램 칸이 빈 줄)은 Q 이미지 편집기에서 온 의견이다.

  [처음 만드는 경우]
  구글 시트를 새로 만들고 위와 같이 코드를 붙여 넣은 뒤, [배포] > [새 배포] > 유형 [웹 앱]
  (다음 사용자로 실행: 나 / 액세스 권한: 모든 사용자)로 배포해 나온 '웹 앱 URL'을 프로그램에 넣는다.
*/

const SHEET_NAME = "의견";

function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(10000);
    const d = JSON.parse(e.postData.contents);

    const rating = Math.max(0, Math.min(5, parseInt(d.rating, 10) || 0));
    const features = Array.isArray(d.features)
      ? d.features.slice(0, 10).map(String).join(", ").slice(0, 100)
      : "";
    const comment = String(d.comment || "").slice(0, 300);
    const version = String(d.version || "").slice(0, 20);
    const app = String(d.app || "").slice(0, 30);

    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sh = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
    if (sh.getLastRow() === 0) {
      sh.appendRow(["시간", "별점", "자주 쓴 기능", "의견", "프로그램 버전", "프로그램"]);
      sh.setFrozenRows(1);
    }
    const when = Utilities.formatDate(new Date(), "Asia/Seoul", "yyyy-MM-dd HH:mm");
    sh.appendRow([when, rating, safe_(features), safe_(comment), safe_(version), safe_(app)]);
    return json_({ ok: true });
  } catch (err) {
    return json_({ ok: false });
  } finally {
    try { lock.releaseLock(); } catch (x) {}
  }
}

// 브라우저로 주소를 열어 봤을 때 "작동 중" 확인용
function doGet() {
  return json_({ ok: true, info: "Q 시리즈 의견 받는 곳" });
}

// 시트에서 =, +, -, @ 로 시작하는 글이 수식으로 실행되지 않게 막는다
function safe_(s) {
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
