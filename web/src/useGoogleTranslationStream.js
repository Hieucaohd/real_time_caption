import { useEffect, useState } from "react";

export default function useGoogleTranslationStream(cid, settings) {
  const [snapshot, setSnapshot] = useState(null);
  const engine = settings.translation_engine;
  const source = settings.translation_source;
  const target = settings.translation_target;
  const key = JSON.stringify([cid, engine, source, target]);
  useEffect(() => {
    setSnapshot(null);
    if (!cid || engine !== "google_web") return;
    let live = true, socket, retry;
    function connect() {
      socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/conversations/${encodeURIComponent(cid)}/translation-stream`);
      socket.onmessage = (event) => {
        if (!live) return;
        try {
          const value = JSON.parse(event.data);
          if (value.conversation_id === cid && value.engine === engine &&
              value.source === source && value.target === target)
            setSnapshot({ key, reading: value.reading });
        } catch { /* A reconnect will recover the persisted snapshot. */ }
      };
      socket.onclose = () => {
        if (!live) return;
        setSnapshot(null); // Use the regular HTTP snapshot while disconnected.
        retry = setTimeout(connect, 1000);
      };
      socket.onerror = () => socket.close();
    }
    connect();
    return () => {
      live = false;
      clearTimeout(retry);
      socket?.close();
    };
  }, [cid, engine, source, target, key]);
  return snapshot?.key === key ? snapshot.reading : null;
}
