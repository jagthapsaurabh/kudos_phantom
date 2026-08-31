import React from 'react';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });
    // Log to console for debugging; could also send to backend /logs endpoint
    console.error('[ErrorBoundary] Uncaught error:', error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    if (this.props.onReset) this.props.onReset();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-[300px] flex items-center justify-center p-6">
          <div className="bg-gray-800 border border-red-900/50 rounded-2xl p-6 max-w-lg w-full">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-8 h-8 rounded-full bg-red-900/40 flex items-center justify-center">
                <span className="text-red-400 text-lg">!</span>
              </div>
              <h2 className="text-lg font-bold text-white">Something went wrong</h2>
            </div>
            <p className="text-sm text-gray-400 mb-3">
              {this.props.fallbackMessage || 'An unexpected error occurred. The error has been logged.'}
            </p>
            {this.state.error && (
              <div className="bg-gray-900 rounded-lg p-3 mb-4 overflow-auto max-h-32">
                <code className="text-xs text-red-300 break-all">
                  {this.state.error.message || String(this.state.error)}
                </code>
              </div>
            )}
            <div className="flex gap-2">
              <button
                onClick={this.handleReset}
                className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold transition"
              >
                Try Again
              </button>
              <button
                onClick={() => window.location.reload()}
                className="px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-white text-sm font-semibold transition"
              >
                Reload Page
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
