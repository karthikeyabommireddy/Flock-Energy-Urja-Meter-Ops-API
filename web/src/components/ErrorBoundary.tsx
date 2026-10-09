import { Component, type ErrorInfo, type ReactNode } from 'react';
import { ErrorState } from './Ui';

interface Props {
  /** Changes when the user navigates: going somewhere else is a fresh start, so the error is cleared. */
  resetKey: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
  resetKey: string;
}

/**
 * Whatever throws while a screen renders (a script chunk that failed to load, an answer in a shape the screen did
 * not expect) ends up here, inside the page shell, instead of unmounting the whole app and leaving a blank page.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey === state.resetKey ? null : { error: null, resetKey: props.resetKey };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('A screen failed to render', error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) return <ErrorState error={this.state.error} onRetry={() => window.location.reload()} />;
    return this.props.children;
  }
}
