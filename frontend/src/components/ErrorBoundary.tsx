import { Component, type ReactNode } from "react";
export default class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: boolean }
> {
  state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  render() {
    return this.state.error ? (
      <main>
        <h1>工作臺遇到顯示問題</h1>
        <p>已儲存的專案與背景任務仍保留在本機服務。請重新開啟頁面。</p>
        <button onClick={() => location.reload()}>重新開啟</button>
      </main>
    ) : (
      this.props.children
    );
  }
}
