# Homebrew tap release

After tagging a release, download the source archive and calculate its SHA-256. Replace `PLACEHOLDER` in the formula, then run `brew update-python-resources packaging/homebrew/travel-mode.rb` to generate the Textual dependency resources. Review the formula and push it to `yulong-ge/homebrew-tap`.
