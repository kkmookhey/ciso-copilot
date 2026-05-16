import SwiftUI

/// Modal sheet for editing a single multi-select category of the stack profile.
/// Mutations are held locally until "Save" — Cancel discards changes.
struct CategoryEditView: View {
    let title: String
    let subtitle: String
    let options: [String]
    let initialSelection: [String]
    let onSave: ([String]) async -> Void

    @State private var working: [String]
    @State private var saving = false
    @Environment(\.dismiss) private var dismiss

    init(
        title: String,
        subtitle: String,
        options: [String],
        selection: [String],
        onSave: @escaping ([String]) async -> Void
    ) {
        self.title = title
        self.subtitle = subtitle
        self.options = options
        self.initialSelection = selection
        self.onSave = onSave
        self._working = State(initialValue: selection)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text(subtitle)
                        .foregroundStyle(.secondary)

                    FlowLayout(spacing: 8) {
                        ForEach(options, id: \.self) { opt in
                            Chip(label: opt, isSelected: working.contains(opt)) {
                                if let idx = working.firstIndex(of: opt) {
                                    working.remove(at: idx)
                                } else {
                                    working.append(opt)
                                }
                            }
                        }
                    }
                }
                .padding()
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .disabled(saving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if saving {
                        ProgressView()
                    } else {
                        Button("Save") {
                            Task {
                                saving = true
                                await onSave(working)
                                saving = false
                                dismiss()
                            }
                        }
                        .disabled(working == initialSelection)
                    }
                }
            }
        }
        .interactiveDismissDisabled(saving)
    }
}

/// Modal sheet for a single-select picker (sector, employee band).
struct SingleSelectEditView: View {
    let title: String
    let subtitle: String
    let options: [String]
    let initialSelection: String?
    let onSave: (String?) async -> Void

    @State private var working: String?
    @State private var saving = false
    @Environment(\.dismiss) private var dismiss

    init(
        title: String,
        subtitle: String,
        options: [String],
        selection: String?,
        onSave: @escaping (String?) async -> Void
    ) {
        self.title = title
        self.subtitle = subtitle
        self.options = options
        self.initialSelection = selection
        self.onSave = onSave
        self._working = State(initialValue: selection)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text(subtitle).foregroundStyle(.secondary)
                    SingleSelectChips(options: options, selection: $working)
                }
                .padding()
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .disabled(saving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if saving {
                        ProgressView()
                    } else {
                        Button("Save") {
                            Task {
                                saving = true
                                await onSave(working)
                                saving = false
                                dismiss()
                            }
                        }
                        .disabled(working == initialSelection)
                    }
                }
            }
        }
        .interactiveDismissDisabled(saving)
    }
}
