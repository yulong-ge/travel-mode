# Template: replace PLACEHOLDER values after each release. Do not submit this file as-is.
class TravelMode < Formula
  include Language::Python::Virtualenv

  desc "One-command macOS battery-saving travel mode"
  homepage "https://github.com/yulong-ge/travel-mode"
  url "https://github.com/yulong-ge/travel-mode/archive/refs/tags/vPLACEHOLDER.tar.gz"
  sha256 "PLACEHOLDER"
  license "MIT"
  depends_on "python@3.12"

  resource "textual" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/textual-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  resource "linkify-it-py" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/linkify_it_py-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  resource "markdown-it-py" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/markdown_it_py-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  resource "mdit-py-plugins" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/mdit_py_plugins-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  resource "mdurl" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/mdurl-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  resource "platformdirs" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/platformdirs-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  resource "pygments" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/pygments-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  resource "rich" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/rich-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  resource "tomlkit" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/tomlkit-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  resource "tomli" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/tomli-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  resource "typing-extensions" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER/typing_extensions-PLACEHOLDER.tar.gz"
    sha256 "PLACEHOLDER"
  end
  # Run `brew update-python-resources packaging/homebrew/travel-mode.rb` to
  # replace the placeholders with exact URLs and hashes before publishing.

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "travel-mode", shell_output("#{bin}/travel-mode --version")
  end
end
