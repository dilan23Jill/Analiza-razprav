import { Component } from 'react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error, info) {
    console.error('UI error:', error, info)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-dark-900 flex items-center justify-center px-6" role="alert">
          <div className="max-w-md text-center">
            <h1 className="text-xl font-bold text-white mb-2">
              {this.props.title || 'Something went wrong'}
            </h1>
            <p className="text-white/50 text-sm mb-6">
              {this.props.message || 'Reload the page or try again later.'}
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-accent-red hover:bg-brand-600 text-pure-white text-sm font-medium rounded-lg transition-colors"
            >
              {this.props.reloadLabel || 'Reload'}
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
