// Share in-flight discovery/creation so simultaneous consumers don't open duplicate tabs.
export function createChatGPTTabLoader(api, post) {
  let scanning = null, opening = null;
  return async function load({ ensure = false } = {}) {
    if (opening) return opening;
    if (!scanning) {
      scanning = api("/chrome-tabs").finally(() => { scanning = null; });
    }
    const tabs = await scanning;
    if (!Array.isArray(tabs)) throw new Error("Không đọc được danh sách tab ChatGPT.");
    if (tabs.length || !ensure) return tabs;
    if (!opening) {
      opening = post("/chrome-tabs", {}).then(tab => {
        if (!tab?.key) throw new Error("Không xác định được tab ChatGPT vừa mở. Hãy thử Refresh lại.");
        // The next Chrome scan may lag behind creation; use the confirmed result.
        return [tab];
      }).finally(() => { opening = null; });
    }
    return opening;
  };
}
