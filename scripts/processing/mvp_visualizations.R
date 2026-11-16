# =============================================================================
# Cancer Screening Priority Mapper - MVP classification figures
# Run after mvp_classification.R. Uses df_classification,
# df_classification_long, cutoff_grid, sensitivity, cutoffs, pctl_scale,
# and out_dir from that script.
#
# Figures
#   1. fig_classes_overall      Class distribution at the default cutoffs
#   2. fig_class_agreement      Agreement across the three burden measures
#   3. fig_class_mix_cutoffs    Class distribution at all 27 cutoff combinations
#   4. fig_heat_tracts          High-priority tracts, every cutoff combination
#   5. fig_heat_women           Women 40-74 in high-priority tracts, every combination
#   6. fig_sens_tracts          National vs urban/rural ranking (equal access and
#   7. fig_sens_women             barrier cutoffs)
#   8. fig_sens_rural_share     Urban/rural mix of high-priority tracts (counts)
# =============================================================================

library(tidyverse)
library(patchwork)
library(scales)

# ---- Shared settings --------------------------------------------------------
# Fixed order and colors so every figure uses the same class colors.
# Warm = need (darker = higher priority); blue = lower priority; greys = unknown.
# Named fig_class_levels so the classification script's class_levels
# (used for integer class codes) is not overwritten.
fig_class_levels <- c("High priority", "Access gap", "Barrier gap", "Monitor",
                      "Lower priority", "Burden suppressed: gaps present",
                      "Burden suppressed: no gaps", "No data")
class_cols <- setNames(c("#b2182b", "#ef6548", "#fdae61", "#fee08b",
                         "#4575b4", "#878787", "#cccccc", "#f2f2f2"),
                       fig_class_levels)

measure_labs <- c(aair       = "AAIR (relative)",
                  cases      = "Cases (absolute)",
                  late_stage = "% late stage")
scale_labs   <- c(nat = "National ranking", ur = "Urban/rural ranking")
scale_name   <- if (pctl_scale == "ur") "urban/rural" else "national"

default_txt <- sprintf("Default cutoffs: burden %sth, access %sth, barriers %sth percentile (%s ranking)",
                       cutoffs[["burden"]], cutoffs[["access"]], cutoffs[["barriers"]], scale_name)

top_n_combos <- 25          # class combinations shown in the agreement plot

snake <- function(x) str_remove(str_replace_all(str_to_lower(x), "[^a-z0-9]+", "_"), "_$")

# Cutoff grid with readable labels
grid_plot <- cutoff_grid |>
  mutate(burden_measure = factor(measure_labs[burden_measure], measure_labs),
         scale          = factor(scale_labs[scale], scale_labs),
         pct_rural      = if_else(hp_urban + hp_rural > 0,
                                  100 * hp_rural / (hp_urban + hp_rural), NA_real_))

# ---- 1. Overall class distribution (default cutoffs) -------------------------
df_long <- df_classification_long |>
  mutate(class          = factor(replace_na(class, "No data"), fig_class_levels),
         burden_measure = factor(measure_labs[burden_measure], measure_labs),
         rurality       = replace_na(rurality, "Unknown"))

overall <- df_long |>
  group_by(burden_measure, rurality, class) |>
  summarise(Tracts        = n(),
            `Women 40-74` = sum(n_women_40_74, na.rm = TRUE), .groups = "drop") |>
  pivot_longer(c(Tracts, `Women 40-74`), names_to = "metric", values_to = "n") |>
  group_by(burden_measure, rurality, metric) |>
  mutate(pct = 100 * n / sum(n)) |>
  ungroup()

p_overall <- ggplot(overall, aes(pct, fct_rev(burden_measure), fill = class)) +
  geom_col(width = 0.7, color = "white", linewidth = 0.2) +
  facet_grid(rurality ~ metric) +
  scale_fill_manual(values = class_cols, drop = FALSE) +
  scale_x_continuous(labels = label_percent(scale = 1), expand = c(0, 0)) +
  theme_minimal(base_size = 11) +
  theme(legend.position = "bottom", panel.grid.major.y = element_blank()) +
  guides(fill = guide_legend(nrow = 3)) +
  labs(title = "Priority classes by burden measure", subtitle = default_txt,
       x = "Share", y = NULL, fill = NULL)

# Table: high-priority tracts and women at the default cutoffs
overall |>
  filter(class == "High priority") |>
  select(burden_measure, rurality, metric, n) |>
  pivot_wider(names_from = c(metric, rurality), values_from = n, values_fill = 0) |>
  print()

# ---- 2. Agreement across the three burden measures (default cutoffs) ---------
combos_all <- df_classification |>
  mutate(across(c(class_aair, class_cases, class_late_stage),
                ~ replace_na(.x, "No data"))) |>
  count(class_aair, class_cases, class_late_stage, sort = TRUE) |>
  mutate(agreement = if_else(class_aair == class_cases & class_cases == class_late_stage,
                             "Full agreement", "Disagreement"),
         pct = 100 * n / sum(n))

n_combos <- nrow(combos_all)
pct_full <- sum(combos_all$pct[combos_all$agreement == "Full agreement"])

combos <- combos_all |>
  slice_head(n = top_n_combos) |>
  mutate(combo_id = fct_rev(fct_inorder(as.character(row_number()))))   # largest at top

combo_grid <- combos |>
  pivot_longer(c(class_aair, class_cases, class_late_stage),
               names_to = "method", names_prefix = "class_", values_to = "class") |>
  mutate(method = factor(measure_labs[method], measure_labs),
         class  = factor(class, fig_class_levels))

p_grid <- ggplot(combo_grid, aes(method, combo_id, fill = class)) +
  geom_tile(color = "white", linewidth = 0.8) +
  scale_fill_manual(values = class_cols, drop = FALSE) +
  scale_x_discrete(position = "top") +
  theme_minimal(base_size = 11) +
  theme(axis.text.y = element_blank(), panel.grid = element_blank(),
        legend.position = "bottom") +
  guides(fill = guide_legend(nrow = 4)) +
  labs(x = NULL, y = sprintf("Top %d of %d combinations (ranked)",
                             nrow(combos), n_combos), fill = NULL)

p_bars <- ggplot(combos, aes(n, combo_id, fill = agreement)) +
  geom_col(width = 0.75) +
  geom_text(aes(label = sprintf("%s (%.1f%%)", comma(n), pct)), hjust = -0.1, size = 3) +
  scale_fill_manual(values = c("Full agreement" = "#2b8cbe", "Disagreement" = "#bdbdbd")) +
  scale_x_continuous(labels = comma, expand = expansion(mult = c(0, 0.3))) +
  theme_minimal(base_size = 11) +
  theme(axis.text.y = element_blank(), panel.grid.major.y = element_blank(),
        legend.position = "bottom") +
  labs(x = "Tracts", y = NULL, fill = NULL)

p_agree <- p_grid + p_bars +
  plot_layout(widths = c(1.2, 2)) +
  plot_annotation(
    title = "Do the burden measures agree on a tract's class?",
    subtitle = sprintf("%s. %.1f%% of tracts have the same class under all three measures; top %d combinations cover %.1f%% of tracts",
                       default_txt, pct_full, nrow(combos), sum(combos$pct)))

# ---- 3. Class mix at every cutoff combination --------------------------------
class_n_cols <- setNames(fig_class_levels, paste0("n_", snake(fig_class_levels)))

class_mix <- grid_plot |>
  filter(scale == scale_labs[[pctl_scale]]) |>
  pivot_longer(all_of(names(class_n_cols)), names_to = "class", values_to = "n") |>
  mutate(class = factor(class_n_cols[class], fig_class_levels),
         combo = sprintf("B%s  A%s  R%s", burden_cut, access_cut, barrier_cut),
         combo = fct_rev(fct_reorder(combo, burden_cut * 1e4 + access_cut * 100 + barrier_cut)),
         combo_face = if_else(current, "bold", "plain")) |>
  group_by(burden_measure, combo) |>
  mutate(pct = 100 * n / sum(n)) |>
  ungroup()

p_class_mix <- ggplot(class_mix, aes(pct, combo, fill = class)) +
  geom_col(width = 0.8, color = "white", linewidth = 0.1) +
  facet_wrap(~ burden_measure, nrow = 1) +
  scale_fill_manual(values = class_cols, drop = FALSE) +
  scale_x_continuous(labels = label_percent(scale = 1), expand = c(0, 0)) +
  theme_minimal(base_size = 10) +
  theme(legend.position = "bottom", panel.grid.major.y = element_blank(),
        axis.text.y = element_text(family = "mono", size = 8)) +
  guides(fill = guide_legend(nrow = 3)) +
  labs(title = "Priority classes at every cutoff combination",
       subtitle = sprintf("B = burden, A = access, R = barriers (percentile cutoffs); %s ranking. Default: B%s A%s R%s",
                          scale_name, cutoffs[["burden"]], cutoffs[["access"]], cutoffs[["barriers"]]),
       x = "Share of tracts", y = NULL, fill = NULL)

# ---- 4-5. Heat maps: every cutoff combination ---------------------------------
# Rows = burden cutoff; columns = burden measure; tiles = access x barrier cutoffs.
plot_heat <- function(y, title, fill_lab) {
  d <- grid_plot |> filter(scale == scale_labs[[pctl_scale]])
  ggplot(d, aes(factor(access_cut), factor(barrier_cut), fill = .data[[y]])) +
    geom_tile(color = "white", linewidth = 0.6) +
    geom_text(aes(label = comma(.data[[y]]),
                  fontface = if_else(current, "bold", "plain")), size = 3) +
    geom_tile(data = filter(d, current), fill = NA, color = "black", linewidth = 0.8) +
    facet_grid(paste0("Burden ", burden_cut, "th") ~ burden_measure) +
    scale_fill_distiller(palette = "YlOrRd", direction = 1, trans = "log1p",
                         labels = comma) +
    theme_minimal(base_size = 11) +
    theme(panel.grid = element_blank(), strip.text.y = element_text(angle = 0)) +
    labs(title = title,
         subtitle = sprintf("%s ranking; outlined = default cutoffs", str_to_sentence(scale_name)),
         x = "Access cutoff (percentile)", y = "Barrier cutoff (percentile)",
         fill = fill_lab)
}

p_heat_tracts <- plot_heat("hp_tracts", "High-priority tracts by cutoff combination", "Tracts")
p_heat_women  <- plot_heat("hp_women_40_74",
                           "Women 40-74 in high-priority tracts by cutoff combination", "Women")

# ---- 6-7. National vs urban/rural ranking (access cutoff = barrier cutoff) ---
sens_plot <- sensitivity |>
  mutate(burden_measure = factor(measure_labs[burden_measure], measure_labs),
         scale          = factor(scale_labs[scale], scale_labs),
         burden_cut     = factor(burden_cut))

plot_sens <- function(y, ylab, y_labels = comma) {
  ggplot(sens_plot, aes(tract_cut, .data[[y]], color = burden_cut, group = burden_cut)) +
    geom_line(linewidth = 0.8) +
    geom_point(size = 1.8) +
    geom_point(data = filter(sens_plot, current),
               shape = 21, size = 4, stroke = 1.1, color = "black", fill = NA) +
    facet_grid(scale ~ burden_measure, scales = "free_y") +
    scale_x_continuous(breaks = sort(unique(sens_plot$tract_cut))) +
    scale_y_continuous(labels = y_labels) +
    scale_color_brewer(palette = "YlOrRd", direction = 1) +
    theme_minimal(base_size = 11) +
    theme(legend.position = "bottom", panel.grid.minor = element_blank()) +
    labs(x = "Access and barrier cutoff (same percentile for both)", y = ylab,
         color = "Burden cutoff (percentile)",
         caption = "Circled point = default cutoffs (shown only when access and barrier defaults are equal)")
}

p_sens_tracts <- plot_sens("hp_tracts", "High-priority tracts") +
  labs(title = "Sensitivity: number of high-priority tracts")
p_sens_women  <- plot_sens("hp_women_40_74", "Women 40-74 in high-priority tracts") +
  labs(title = "Sensitivity: screening-eligible women reached")

# ---- 8. Urban/rural mix of high-priority tracts (counts) ---------------------
base_rural <- with(df_classification, 100 * mean(rurality[!is.na(rurality)] == "Rural"))

sens_mix <- sens_plot |>
  mutate(pct_rural = if_else(hp_urban + hp_rural > 0,
                             100 * hp_rural / (hp_urban + hp_rural), NA_real_)) |>
  pivot_longer(c(hp_urban, hp_rural), names_to = "rurality", values_to = "n",
               names_prefix = "hp_") |>
  mutate(rurality = factor(str_to_title(rurality), c("Rural", "Urban")))

sens_mix_lab <- sens_mix |>
  distinct(scale, burden_measure, burden_cut, tract_cut, pct_rural, hp_tracts)

p_sens_rural <- ggplot(sens_mix, aes(factor(tract_cut), n)) +
  geom_col(aes(fill = rurality), width = 0.75) +
  geom_text(data = sens_mix_lab,
            aes(y = hp_tracts, label = if_else(is.na(pct_rural), "0 tracts",
                                               sprintf("%.0f%%", pct_rural))),
            vjust = -0.3, size = 2.6) +
  facet_grid(scale + burden_cut ~ burden_measure, scales = "free_y",
             labeller = labeller(burden_cut = \(x) paste0("Burden ", x, "th"))) +
  scale_fill_manual(values = c(Rural = "#1b7837", Urban = "#762a83")) +
  scale_y_continuous(labels = comma, expand = expansion(mult = c(0, 0.25))) +
  theme_minimal(base_size = 10) +
  theme(legend.position = "bottom", panel.grid.major.x = element_blank(),
        strip.text.y = element_text(angle = 0)) +
  labs(title = "Sensitivity: urban/rural mix of high-priority tracts",
       subtitle = sprintf("Labels = %% rural among high-priority tracts. Rural share of all tracts: %.0f%%",
                          base_rural),
       x = "Access and barrier cutoff (same percentile for both)",
       y = "High-priority tracts", fill = NULL)

# ---- Display and save -----------------------------------------------------------
figs <- list(fig_classes_overall   = list(p_overall,     10, 7),
             fig_class_agreement   = list(p_agree,       11, 8),
             fig_class_mix_cutoffs = list(p_class_mix,   12, 9),
             fig_heat_tracts       = list(p_heat_tracts, 11, 8),
             fig_heat_women        = list(p_heat_women,  11, 8),
             fig_sens_tracts       = list(p_sens_tracts, 10, 6),
             fig_sens_women        = list(p_sens_women,  10, 6),
             fig_sens_rural_share  = list(p_sens_rural,  11, 11))

walk(figs, \(f) print(f[[1]]))

iwalk(figs, \(f, nm) ggsave(file.path(out_dir, paste0(nm, ".png")), f[[1]],
                            width = f[[2]], height = f[[3]], dpi = 300, bg = "white"))
