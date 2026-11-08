package com.outerheaven.catlord.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.outerheaven.catlord.data.model.CatlordVoiceNote
import com.outerheaven.catlord.data.model.SyncStatus
import com.outerheaven.catlord.speech.SpeechState
import com.outerheaven.catlord.ui.MainViewModel
import com.outerheaven.catlord.ui.theme.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun VoiceIngestScreen(
    viewModel: MainViewModel,
    onRequestMicrophonePermission: () -> Unit
) {
    val speechState by viewModel.speechState.collectAsState()
    val selectedCategory by viewModel.selectedCategory.collectAsState()
    val title by viewModel.noteTitle.collectAsState()
    val content by viewModel.noteContent.collectAsState()
    val notes by viewModel.notes.collectAsState()
    val isSyncing by viewModel.isSyncing.collectAsState()
    val isConnected by viewModel.isConnected.collectAsState()
    val statusMessage by viewModel.statusMessage.collectAsState()

    val isListening = speechState is SpeechState.Listening

    // Pulsier-Animation bei aktiver Aufnahme
    val infiniteTransition = rememberInfiniteTransition(label = "pulse")
    val pulseScale by infiniteTransition.animateFloat(
        initialValue = 1.0f,
        targetValue = if (isListening) 1.25f else 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(600, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "scale"
    )

    LazyColumn(
        modifier = consciencePaddingModifier(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // Status Banner
        item {
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = if (isListening) NeonCyan.copy(alpha = 0.15f) else DarkSurfaceVariant
                ),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(14.dp)
                                .background(
                                    when {
                                        isListening -> DangerRed
                                        isConnected -> NeonGreen
                                        else -> AmberGold
                                    },
                                    CircleShape
                                )
                        )
                        Text(
                            text = when (val state = speechState) {
                                is SpeechState.Listening -> "Aufnahme läuft (de-DE)..."
                                is SpeechState.Processing -> "Verarbeite Spracheingabe..."
                                is SpeechState.Error -> "Fehler: ${state.errorMessage}"
                                else -> if (isConnected) "Bereit für Ideen // Bridge aktiv" else "Offline // Lokale Queue aktiv"
                            },
                            style = MaterialTheme.typography.bodyMedium,
                            fontWeight = FontWeight.Medium,
                            color = if (isListening) DangerRed else TextPrimary
                        )
                    }

                    if (isSyncing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            color = NeonCyan,
                            strokeWidth = 2.dp
                        )
                    }
                }
            }
        }

        // Kategorie-Chips
        item {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    text = "Kategorie auswählen",
                    style = MaterialTheme.typography.titleMedium,
                    color = TextSecondary
                )
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    viewModel.categories.forEach { cat ->
                        val isSelected = cat == selectedCategory
                        FilterChip(
                            selected = isSelected,
                            onClick = { viewModel.setCategory(cat) },
                            label = { Text(cat) },
                            colors = FilterChipDefaults.filterChipColors(
                                selectedContainerColor = NeonCyan,
                                selectedLabelColor = DarkBackground,
                                containerColor = DarkSurfaceVariant,
                                labelColor = TextPrimary
                            ),
                            border = FilterChipDefaults.filterChipBorder(
                                enabled = true,
                                selected = isSelected,
                                borderColor = if (isSelected) NeonCyan else DarkSurfaceElevated
                            )
                        )
                    }
                }
            }
        }

        // Titel & Inhalt Editor
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkSurface),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    OutlinedTextField(
                        value = title,
                        onValueChange = { viewModel.setTitle(it) },
                        label = { Text("Titel (optional)") },
                        placeholder = { Text("z.B. Elliot Level 3 Taktik...") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = NeonCyan,
                            unfocusedBorderColor = DarkSurfaceElevated,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary,
                            cursorColor = NeonCyan
                        )
                    )

                    OutlinedTextField(
                        value = content,
                        onValueChange = { viewModel.setContent(it) },
                        label = { Text("Notiz / Spracheingabe") },
                        placeholder = { Text("Tippen oder Mikrofon drücken zum Diktieren...") },
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = 120.dp, max = 240.dp),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = NeonCyan,
                            unfocusedBorderColor = DarkSurfaceElevated,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary,
                            cursorColor = NeonCyan
                        )
                    )

                    // Aufnahme & Action Bar
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        // Mic Button mit Animation
                        IconButton(
                            onClick = {
                                onRequestMicrophonePermission()
                                viewModel.toggleSpeechRecognition()
                            },
                            modifier = Modifier
                                .scale(if (isListening) pulseScale else 1f)
                                .background(
                                    if (isListening) DangerRed else NeonCyan,
                                    CircleShape
                                )
                                .size(52.dp)
                        ) {
                            Icon(
                                imageVector = if (isListening) Icons.Default.Stop else Icons.Default.Mic,
                                contentDescription = "Spracheingabe",
                                tint = if (isListening) Color.White else DarkBackground,
                                modifier = Modifier.size(28.dp)
                            )
                        }

                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (content.isNotBlank()) {
                                OutlinedButton(
                                    onClick = { viewModel.setContent("") },
                                    colors = ButtonDefaults.outlinedButtonColors(contentColor = DangerRed)
                                ) {
                                    Icon(Icons.Default.Clear, contentDescription = "Leeren", modifier = Modifier.size(16.dp))
                                    Spacer(Modifier.width(4.dp))
                                    Text("Leeren")
                                }
                            }

                            Button(
                                onClick = { viewModel.saveCurrentNote() },
                                colors = ButtonDefaults.buttonColors(
                                    containerColor = AmberGold,
                                    contentColor = DarkBackground
                                )
                            ) {
                                Icon(Icons.Default.Save, contentDescription = "Speichern", modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(6.dp))
                                Text("Speichern & Sync", fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                }
            }
        }

        // Status Toast / Feedback Message
        item {
            AnimatedVisibility(visible = statusMessage != null) {
                Card(
                    colors = CardDefaults.cardColors(containerColor = CyberViolet.copy(alpha = 0.2f)),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 16.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text(
                            text = statusMessage ?: "",
                            style = MaterialTheme.typography.bodyMedium,
                            color = CyberViolet
                        )
                        IconButton(onClick = { viewModel.clearStatusMessage() }) {
                            Icon(Icons.Default.Close, contentDescription = "Schließen", tint = CyberViolet, modifier = Modifier.size(18.dp))
                        }
                    }
                }
            }
        }

        // Lokale Notizen Header & Liste
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "Gespeicherte Notizen (${notes.size})",
                    style = MaterialTheme.typography.titleLarge,
                    color = TextPrimary
                )

                TextButton(
                    onClick = { viewModel.syncAllNotes() },
                    enabled = !isSyncing
                ) {
                    Icon(Icons.Default.Sync, contentDescription = "Alle syncen", tint = NeonCyan, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Alle Syncen", color = NeonCyan)
                }
            }
        }

        if (notes.isEmpty()) {
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = DarkSurface),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Icon(Icons.Default.Lightbulb, contentDescription = null, tint = TextSecondary, modifier = Modifier.size(36.dp))
                        Text("Noch keine Voice-Notes vorhanden.", color = TextSecondary)
                        Text("Sprich eine neue Idee für Story, Gameplay oder Charaktere ein!", color = TextDisabled, fontSize = 12.sp)
                    }
                }
            }
        } else {
            items(notes, key = { it.id }) { note ->
                NoteCard(
                    note = note,
                    onDelete = { viewModel.deleteNote(note.id) },
                    onRetrySync = { viewModel.syncAllNotes() }
                )
            }
        }
    }
}

@Composable
fun NoteCard(
    note: CatlordVoiceNote,
    onDelete: () -> Unit,
    onRetrySync: () -> Unit
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = DarkSurface),
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Surface(
                        color = NeonCyan.copy(alpha = 0.15f),
                        shape = RoundedCornerShape(6.dp)
                    ) {
                        Text(
                            text = note.category.uppercase(),
                            color = NeonCyan,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                        )
                    }
                    Text(
                        text = note.title,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary
                    )
                }

                // Sync Status Badge
                Row(verticalAlignment = Alignment.CenterVertically) {
                    when (note.syncStatus) {
                        SyncStatus.SYNCED -> {
                            Icon(Icons.Default.CheckCircle, contentDescription = "Gesynct", tint = NeonGreen, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Gesynct", color = NeonGreen, fontSize = 11.sp)
                        }
                        SyncStatus.PENDING_SYNC -> {
                            IconButton(onClick = onRetrySync, modifier = Modifier.size(24.dp)) {
                                Icon(Icons.Default.CloudUpload, contentDescription = "Ausstehend", tint = AmberGold, modifier = Modifier.size(16.dp))
                            }
                            Text("Ausstehend", color = AmberGold, fontSize = 11.sp)
                        }
                        SyncStatus.FAILED -> {
                            IconButton(onClick = onRetrySync, modifier = Modifier.size(24.dp)) {
                                Icon(Icons.Default.ErrorOutline, contentDescription = "Fehler", tint = DangerRed, modifier = Modifier.size(16.dp))
                            }
                            Text("Fehlgeschlagen", color = DangerRed, fontSize = 11.sp)
                        }
                    }
                }
            }

            Text(
                text = note.content,
                style = MaterialTheme.typography.bodyMedium,
                color = TextSecondary,
                maxLines = 4
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = note.formattedDate(),
                    style = MaterialTheme.typography.labelSmall,
                    color = TextDisabled
                )

                IconButton(
                    onClick = onDelete,
                    modifier = Modifier.size(28.dp)
                ) {
                    Icon(Icons.Default.DeleteOutline, contentDescription = "Löschen", tint = TextSecondary, modifier = Modifier.size(18.dp))
                }
            }
        }
    }
}

@Composable
fun consciencePaddingModifier(): Modifier = Modifier.fillMaxSize()
